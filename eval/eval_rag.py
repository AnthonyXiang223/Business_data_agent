"""RAG 检索评估 CLI：分阶段 Recall/Precision/MRR + 低置信守卫指标（零 LLM 调用）。

定位（阶段 7 检索评估集）：测 rag/ 检索管道（向量+BM25 → RRF → rerank →
低置信守卫）的检索质量，按层定位丢块位置：
- RRF Recall@20：融合粗筛后 golden 是否被捞出（召回层问题）
- Rerank Recall@3 / Precision@3 / MRR：精排后 golden 是否进最终窗口（排序层问题）
- 守卫：正样本 golden 全进 top-3 但守卫 miss = 漏报（正确结果被拦截）；
  负样本守卫 hit = 误报（库里没有却放行）
- 校准段：正/负样本 rerank 最高分分布 → 建议 LOW_CONF_THRESHOLD 区间（只报告不改）

与 eval_retrieval.py（取数评估）的区别：检索管道确定性（无温度参数、无 LLM），
无 judge、无断点状态、无并行——单文件 CLI 足够。
评测调 rag_tools._search_raw（绕过 Redis 缓存测真实管道），管道参数单一来源，
不复制检索逻辑。golden 锚定 (doc, chunk_index) + snippet 加载校验：
chunk_id 在 kb_build 原子重建后会变，锚 id 会静默错标（见 data/rag_eval_cases.yaml）。

运行：cd eval && PYTHONPATH=../rag ../.venv/bin/python eval_rag.py [--only id] [--verbose]
"""

import argparse
import os
import sys
from pathlib import Path

# 与 eval_retrieval.py 同约定：import 工具前先设 LangSmith 项目环境
os.environ.setdefault("LANGSMITH_PROJECT", "deepagents-eval")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import psycopg  # noqa: E402
import yaml  # noqa: E402
from pgvector.psycopg import register_vector  # noqa: E402

# rag_tools 在 rag/（与 embedder 同目录，模型从 rag/models 加载），垫 rag 目录再导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "rag"))
from rag_tools import (  # noqa: E402
    LOW_CONF_THRESHOLD,
    POSTGRES_URL,
    RERANK_TOP_N,
    _guard,
    _search_raw,
)

CASES_FILE = Path(__file__).resolve().parent.parent / "data" / "rag_eval_cases.yaml"
_REQUIRED = {"id", "expect", "question", "golden"}


# ---------- 题目加载与校验 ----------

def load_cases() -> list[dict]:
    """加载题目 + 结构校验（yaml.safe_load，铁律 5）。"""
    raw = yaml.safe_load(CASES_FILE.read_text(encoding="utf-8"))
    cases, seen = [], set()
    for c in raw:
        missing = _REQUIRED - set(c)
        if missing:
            sys.exit(f"题目 {c.get('id', '?')} 缺字段: {sorted(missing)}")
        if c["id"] in seen:
            sys.exit(f"题目 id 重复: {c['id']}")
        seen.add(c["id"])
        if c["expect"] not in ("hit", "miss"):
            sys.exit(f"题目 {c['id']} expect 非法: {c['expect']!r}（应为 hit/miss）")
        golden = c.get("golden") or []
        related = c.get("related") or []
        for field, lst in (("golden", golden), ("related", related)):
            for i, a in enumerate(lst):
                if not {"doc", "index"} <= set(a):
                    sys.exit(f"题目 {c['id']}.{field}[{i}] 缺 doc/index")
        if c["expect"] == "miss" and golden:
            sys.exit(f"负样本 {c['id']} 不应有 golden")
        if c["expect"] == "hit" and not golden:
            sys.exit(f"正样本 {c['id']} 必须有 golden")
        cases.append({**c, "golden": golden, "related": related,
                      "diagnostic": bool(c.get("diagnostic"))})
    return cases


def validate_anchors(cases: list[dict], conn) -> None:
    """加载时校验锚点真实存在（对齐 eval_dataset golden_sql 加载执行约定）。

    (doc, index) 查不到、或 content 不以 snippet 开头 → 立即退出并指出哪题哪块：
    docx 内容改动导致 chunk_index 漂移时，评估必须失败而不是静默错标。
    """
    for c in cases:
        for field in ("golden", "related"):
            for i, a in enumerate(c[field]):
                row = conn.execute(
                    "SELECT content FROM kb_chunks WHERE doc_id=%s AND chunk_index=%s",
                    (a["doc"], a["index"]),
                ).fetchone()
                if row is None:
                    sys.exit(
                        f"锚定失败: {c['id']}.{field}[{i}] "
                        f"{a['doc']} idx={a['index']} 在 kb_chunks 不存在"
                    )
                content = " ".join(row[0].split())
                snippet = " ".join(str(a.get("snippet", "")).split())
                if snippet and not content.startswith(snippet):
                    sys.exit(
                        f"snippet 不匹配: {c['id']}.{field}[{i}] {a['doc']} idx={a['index']}\n"
                        f"  库中开头: {content[:40]}\n  标注开头: {snippet[:40]}"
                    )


# ---------- 判分 ----------

def _short_doc(doc: str) -> str:
    return "口径" if "口径" in doc else "字段"


def _ranks(ids: list[int], golden: list[int]) -> dict[int, int]:
    """golden chunk 在结果列表中的排名（1 起，缺席 = 0）。"""
    pos = {cid: i + 1 for i, cid in enumerate(ids)}
    return {cid: pos.get(cid, 0) for cid in golden}


def run_case(case: dict, conn, id2row, cid2anchor) -> dict:
    """跑一道题：完整管道一次（_search_raw，绕缓存），按层判分。"""
    res = _search_raw(conn, id2row, case["question"])
    fused_ids = [cid for cid, _ in res["fused"]]
    top3 = res["rerank"]  # [((cid, content), score)]
    top3_ids = [cid for (cid, _c), _s in top3]
    best = top3[0][1] if top3 else 0.0
    guard = _guard(best, LOW_CONF_THRESHOLD)

    golden = [cid2anchor[(a["doc"], a["index"])] for a in case["golden"]]
    relevant = golden + [cid2anchor[(a["doc"], a["index"])] for a in case["related"]]
    out = {
        "question": case["question"],
        "best": best,
        "guard": guard,
        "golden_cids": golden,
        "golden_labels": [
            f"{_short_doc(a['doc'])}:{a['index']}" for a in case["golden"]
        ],
        "fused_ranks": _ranks(fused_ids, golden),
        "top3_ranks": _ranks(top3_ids, golden),
        "top3": [(cid, s) for (cid, _c), s in top3],
        "rrf_recall": None, "recall3": None, "precision3": None, "mrr": None,
        "guard_ok": None, "leak": False, "false_hit": False,
    }
    if case["expect"] == "miss":
        out["guard_ok"] = guard == "miss"
        out["false_hit"] = guard == "hit"  # 误报：库里没有却放行
        return out

    hit_fused = sum(1 for r in out["fused_ranks"].values() if r)
    hit3 = sum(1 for r in out["top3_ranks"].values() if r)
    out["rrf_recall"] = hit_fused / len(golden)
    out["recall3"] = hit3 / len(golden)
    out["precision3"] = len(set(relevant) & set(top3_ids)) / RERANK_TOP_N
    out["mrr"] = sum(1 / r for r in out["top3_ranks"].values() if r) / len(golden)
    out["leak"] = out["recall3"] == 1.0 and guard == "miss"  # 漏报：正确结果被守卫拦截
    out["guard_ok"] = guard == "hit"
    return out


# ---------- 报告 ----------

def _frac(x) -> str:
    return "-" if x is None else f"{x:.2f}"


def _mean(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _guard_cell(case: dict, r: dict) -> str:
    if case["expect"] == "miss":
        if r["false_hit"]:
            return "hit ⚠误报"
        return "miss ✓"
    if r["leak"]:
        return "miss ⚠漏报"
    if r["guard"] == "miss":
        return "miss"
    return "hit ✓"


def _print_case_detail(case: dict, r: dict, cid2label) -> None:
    """--verbose：每题 golden 各层排名 + top-3 构成 + rerank 分数细节。"""
    print(f"\n[{case['id']}] {case['question']}")
    for label, cid in zip(r["golden_labels"], r["golden_cids"]):
        fr, tr = r["fused_ranks"][cid], r["top3_ranks"][cid]
        tr_str = f"rerank #{tr}" if tr else "rerank 未进 top-3"
        print(f"  golden {label}: RRF #{fr or '未进'} · {tr_str}")
    items = " | ".join(
        f"#{i} {_short_doc(cid2label[cid][0])}:{cid2label[cid][1]} ({s:.2f})"
        for i, (cid, s) in enumerate(r["top3"], 1)
    )
    print(f"  top-3: {items}")
    print(f"  最高分 {r['best']:.2f} · 守卫 {r['guard']}")


def print_report(cases: list[dict], results: list[dict], total_chunks: int) -> None:
    main = [(c, r) for c, r in zip(cases, results) if not c["diagnostic"]]
    diag = [(c, r) for c, r in zip(cases, results) if c["diagnostic"]]
    pos = [(c, r) for c, r in main if c["expect"] == "hit"]
    neg = [(c, r) for c, r in main if c["expect"] == "miss"]

    print(f"\n==== RAG 检索评估（{len(cases)} 题 · kb {total_chunks} chunks · 零 LLM 调用）")
    print(f"管道: 向量/BM25 top-50 → RRF top-20 → rerank top-{RERANK_TOP_N} · "
          f"守卫阈值 {LOW_CONF_THRESHOLD}")
    print(f"{'题目':<18} {'期望':<4} {'RRF@20':<7} {'RR@3':<6} {'PR@3':<6} "
          f"{'MRR':<6} {'守卫':<11} {'最高分'}")
    for c, r in main:
        print(f"{c['id']:<18} {c['expect']:<4} {_frac(r['rrf_recall']):<7} "
              f"{_frac(r['recall3']):<6} {_frac(r['precision3']):<6} "
              f"{_frac(r['mrr']):<6} {_guard_cell(c, r):<11} {r['best']:.2f}")

    if not pos:
        return
    print(f"\n---- 汇总（正样本 {len(pos)} 题）")
    print(f"  平均 RRF Recall@20 = {_mean([r['rrf_recall'] for _, r in pos]):.2f}")
    print(f"  平均 Rerank Recall@3 = {_mean([r['recall3'] for _, r in pos]):.2f}")
    print(f"  平均 Precision@3 = {_mean([r['precision3'] for _, r in pos]):.2f}")
    print(f"  平均 MRR = {_mean([r['mrr'] for _, r in pos]):.2f}")
    leaks = sum(1 for _, r in pos if r["leak"])
    false_hits = sum(1 for _, r in neg if r["false_hit"])
    print(f"  守卫: 漏报 {leaks}（golden 全进 top-3 但被拦截） · "
          f"误报 {false_hits}（负样本被放行）")

    print(f"\n---- 校准（LOW_CONF_THRESHOLD = {LOW_CONF_THRESHOLD}，只报告不改）")
    hit_scores = [r["best"] for _, r in pos if r["recall3"] == 1.0]
    neg_scores = [r["best"] for _, r in neg]
    if hit_scores:
        print(f"  正样本(golden 全进 top-3)最高分: "
              f"min={min(hit_scores):.2f} median={sorted(hit_scores)[len(hit_scores)//2]:.2f} "
              f"max={max(hit_scores):.2f}")
    if neg_scores:
        print(f"  负样本最高分: max={max(neg_scores):.2f}")
    if hit_scores and neg_scores:
        lo, hi = max(neg_scores), min(hit_scores)
        if lo < hi:
            print(f"  建议阈值区间: ({lo:.2f}, {hi:.2f})——高于全部负样本、低于全部正样本")
        else:
            print(f"  ⚠ 区间倒挂 [{hi:.2f}, {lo:.2f}]：当前阈值无法同时区分正负样本，"
                  f"需改进管道/模型而非调阈值")

    failed = [(c, r) for c, r in pos if r["recall3"] < 1.0]
    if failed:
        print(f"\n---- 失败明细（golden 未进最终 top-3 的题目）")
        for c, r in failed:
            print(f"[{c['id']}] {c['question']}")
            for label, cid in zip(r["golden_labels"], r["golden_cids"]):
                fr, tr = r["fused_ranks"][cid], r["top3_ranks"][cid]
                if not fr:
                    where = "召回层丢（RRF 融合就没捞到）"
                elif fr > 10:
                    # rerank 候选只取 fused[:10]（rag_tools._search_raw），
                    # RRF 排在 10 名开外的 golden 进不了精排
                    where = "rerank 候选截断丢（fused[:10]，RRF 已排 #%d）" % fr
                else:
                    where = "rerank 排序丢（进了候选但没排进 top-3）"
                print(f"  golden {label}: RRF #{fr or '未进'} · "
                      f"rerank {'#' + str(tr) if tr else '未进 top-3'}（{where}）")

    if diag:
        print(f"\n---- 诊断用例（不计入汇总）")
        for c, r in diag:
            print(f"[{c['id']}] {c['question']}")
            print(f"  RRF@20={_frac(r['rrf_recall'])} RR@3={_frac(r['recall3'])} "
                  f"PR@3={_frac(r['precision3'])} MRR={_frac(r['mrr'])} "
                  f"守卫={r['guard']} 最高分={r['best']:.2f}")


# ---------- CLI ----------

def main() -> None:
    ap = argparse.ArgumentParser(description="RAG 检索评估（零 LLM 调用，确定性）")
    ap.add_argument("--only", default="", help="只跑指定题目 id（逗号分隔）")
    ap.add_argument("--verbose", action="store_true",
                    help="打印每题各阶段 golden 排名细节")
    args = ap.parse_args()

    cases = load_cases()
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        unknown = wanted - {c["id"] for c in cases}
        if unknown:
            sys.exit(f"--only 指定了不存在的题目: {sorted(unknown)}")
        cases = [c for c in cases if c["id"] in wanted]

    # 单连接跑全程：校验锚点 + 构建 id2row/cid2anchor + 逐题检索
    with psycopg.connect(POSTGRES_URL) as conn:
        register_vector(conn)
        rows = conn.execute(
            "SELECT chunk_id, content, section_path, doc_id, chunk_index FROM kb_chunks"
        ).fetchall()
        if not rows:
            sys.exit("kb_chunks 为空：请先运行 kb_build.py 入库")
        validate_anchors(cases, conn)
        id2row = {r[0]: r[:4] for r in rows}          # cid -> (cid, content, section_path, doc_id)
        cid2anchor = {(r[3], r[4]): r[0] for r in rows}  # (doc_id, chunk_index) -> cid
        cid2label = {r[0]: (r[3], r[4]) for r in rows}   # cid -> (doc_id, chunk_index)

        results = []
        for i, c in enumerate(cases, 1):
            r = run_case(c, conn, id2row, cid2anchor)
            results.append(r)
            if args.verbose:
                _print_case_detail(c, r, cid2label)
            else:
                print(f"[{i}/{len(cases)}] {c['id']} done", flush=True)
        print_report(cases, results, len(rows))


if __name__ == "__main__":
    main()
