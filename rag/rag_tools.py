"""Agent 工具：search_knowledge——检索业务知识库（口径文档/字段说明文档）原文片段。

与 rag/kb_search.py（CLI 调试入口）的关系：同一套检索逻辑（多路召回 + RRF + rerank），
但本文件按"对外工具"标准封装（铁律 2：工具内外之分）：
- 函数 docstring 是 LLM 的说明书，必须写清参数与用法
- 检索管道细节（向量/BM25/RRF/rerank）全部 _ 前缀，LLM 不可见不可绕
- 模型懒加载：首次调用才加载 bge-m3 / reranker（共约 7G 内存、十几秒），
  agent 启动不付这个成本
- 返回紧凑截断，防爆上下文
- 可观测：管道每级挂 @traceable span（LangSmith），召回断点逐级可查
- 检索结果缓存（Redis）：kb 版本 + 归一化 query 作 key，命中跳过整条管道
  （embedding/pgvector/BM25/rerank 全免）；Redis 不可用自动降级为直查
"""

import os
import sys
from pathlib import Path

# agent/redis_cache.py 是项目根目录下的兄弟包（项目根未必在 sys.path：
# eval 上下文只把 rag 目录垫进来），垫一次再导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import redis_cache  # noqa: E402

import numpy as np
import psycopg
from dotenv import load_dotenv
from jieba import lcut
from langsmith import traceable
from pgvector.psycopg import register_vector
from rank_bm25 import BM25Okapi

from embedder import embed_query

load_dotenv()

POSTGRES_URL = os.environ["POSTGRES_URL"]
RERANK_MODEL_DIR = Path(__file__).parent / "models" / "bge-reranker-v2-m3"
RRF_K = 60  # RRF 平滑常数（论文默认 60）

# 粗筛池放宽：CLI 调试时 BM25 top-20 曾把正确 chunk（排名 31/45）截掉——
# 两级截断（BM25 截断 + RRF 截断）会放大单路失误。
# 当前知识库只有几十个 chunk，放宽到 50 无成本；阈值后续用评估集标定。
VEC_TOP_K = 50
BM25_TOP_K = 50
RRF_TOP_K = 20
RERANK_TOP_N = 3
LOW_CONF_THRESHOLD = 0.3  # rerank 最高分低于它 → 判定"库里没有"，防 LLM 编造口径

_reranker = None  # 单例懒加载，与 embedder.get_model 同一约定


def _get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder(str(RERANK_MODEL_DIR))
    return _reranker


# ---------- 检索管道（内部，LLM 不可见） ----------
# 每级挂 @traceable span（LangSmith）：输入只记可读摘要（向量/连接/长文本
# 不进 trace 防膨胀），输出记完整中间结果（chunk_id + 分数）——
# 召回断点在 trace 树里可逐级定位：vector/BM25 哪路没召回 → RRF 是否融合
# 错 → rerank 分数分布 → 阈值裁决。

@traceable(run_type="retriever", name="vector_search",
           process_inputs=lambda inputs: {"top_k": inputs["top_k"]})
def _vector_search(conn, query_vec, top_k=VEC_TOP_K):
    """路 A：pgvector 余弦距离升序，返回 [(chunk_id, 相似度)]。"""
    rows = conn.execute(
        """SELECT chunk_id, 1 - (embedding <=> %s) AS score
           FROM kb_chunks ORDER BY embedding <=> %s LIMIT %s""",
        (query_vec, query_vec, top_k),
    ).fetchall()
    return [(r[0], float(r[1])) for r in rows]


@traceable(run_type="retriever", name="bm25_search",
           process_inputs=lambda inputs: {"query": inputs["query"], "top_k": inputs["top_k"]})
def _bm25_search(conn, query, top_k=BM25_TOP_K):
    """路 B：应用内存 BM25（jieba 分词），返回 [(chunk_id, 得分)]。"""
    rows = conn.execute("SELECT chunk_id, content FROM kb_chunks").fetchall()
    corpus = [lcut(r[1]) for r in rows]
    scores = BM25Okapi(corpus).get_scores(lcut(query))
    order = np.argsort(-np.array(scores))[:top_k]
    return [(rows[i][0], float(scores[i])) for i in order]


@traceable(run_type="chain", name="rrf_fuse",
           process_inputs=lambda inputs:
           {"list_lens": [len(lst) for lst in inputs["ranked_lists"]], "k": inputs["k"]})
def _rrf_fuse(ranked_lists, k=RRF_TOP_K):
    """RRF：融合分 = Σ 1/(RRF_K + 排名)，各路分数不可比，只比排名。"""
    fused: dict[int, float] = {}
    for lst in ranked_lists:
        for rank, (cid, _score) in enumerate(lst):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:k]


@traceable(run_type="chain", name="rerank",
           process_inputs=lambda inputs:
           {"query": inputs["query"], "candidate_ids": [c[0] for c in inputs["candidates"]],
            "top_n": inputs["top_n"]})
def _rerank(query, candidates, top_n=RERANK_TOP_N):
    """cross-encoder 精排：只对粗筛后的少数候选逐对打分（先粗筛后精选）。"""
    scores = _get_reranker().predict([(query, c[1]) for c in candidates])
    order = np.argsort(-np.array(scores))[:top_n]
    return [(candidates[i], float(scores[i])) for i in order]


@traceable(run_type="chain", name="threshold_guard")
def _guard(best_score: float, threshold: float) -> str:
    """低置信守卫：rerank 最高分 vs 阈值 → hit / miss（trace 可见的裁决节点）。"""
    return "hit" if best_score >= threshold else "miss"


def _search_raw(conn, id2row, query: str) -> dict:
    """完整检索管道（embed → 双路 → RRF → rerank），返回各阶段结果。

    内部函数（铁律 2）：LLM 不可见。评估脚本（eval/eval_rag.py）直接调它
    观测各阶段召回，与 search_knowledge 共用同一份管道参数，不复制逻辑。
    注意：不走 Redis 缓存（缓存是 search_knowledge 入口层的事）——
    评估测的是真实检索质量，不受缓存状态影响。

    返回 {"vector": [(cid, 相似度)], "bm25": [(cid, 得分)],
          "fused": [(cid, RRF分)], "rerank": [((cid, content), 相关分)]}
    """
    qv = embed_query(query)
    vec = _vector_search(conn, qv)
    bm25 = _bm25_search(conn, query)
    fused = _rrf_fuse([vec, bm25])
    candidates = [(cid, id2row[cid][1]) for cid, _ in fused[:10]]
    reranked = _rerank(query, candidates) if candidates else []
    return {"vector": vec, "bm25": bm25, "fused": fused, "rerank": reranked}


# ---------- 对外工具 ----------

@traceable(run_type="chain", name="search_knowledge")
def search_knowledge(query: str) -> str:
    """检索业务知识库（指标口径文档、字段说明文档）中最相关的原文片段。

    什么时候用：
    - 需要口径定义/计算逻辑/字段含义/取值范围等权威原文佐证结论时
    - get_metric_definitions 返回的口径较简略，需要看文档全文细节时

    用法：
    - 先把用户口语归一成标准指标名再传入（先调 get_metric_definitions
      借 aliases 归一，如"每个买家平均花了多少钱"→"人均消费金额"）；
      不要直接传用户原话——口语改写检索效果差
    - 用归一后的自然问句检索（如"人均消费金额的计算公式是什么"）；
      不要用空格拼接的关键词（如"人均消费金额 计算公式"）——
      实测 rerank 模型对关键词式查询打分极低（0.10），会误触发"未找到"
    - 本工具只返回文档片段，不是数值结果——最终数值必须用 query_data 现查
    - 返回片段与口径库（get_metric_definitions）不一致时，提示冲突并向用户确认

    Args:
        query: 检索词（标准业务术语，可含"计算公式/定义/取值范围"等限定词）
    """
    with psycopg.connect(POSTGRES_URL) as conn:
        register_vector(conn)
        # kb 版本 = chunk 总数 + 最大 chunk_id（任一变化 = 知识库重建过，
        # 旧缓存随 key 里的版本号自然失效）。空库不入缓存
        count, max_cid = conn.execute(
            "SELECT count(*), COALESCE(max(chunk_id), 0) FROM kb_chunks"
        ).fetchone()
        if not count:
            return "知识库为空：请先运行 kb_build.py 入库"
        cache_key = f"rag:v{count}:{max_cid}:{redis_cache.digest(query)}"
        cached = redis_cache.get(cache_key)
        if cached is not None:
            return cached

        id2row = {
            r[0]: r for r in conn.execute(
                "SELECT chunk_id, content, section_path, doc_id FROM kb_chunks")
        }

        top = _search_raw(conn, id2row, query)["rerank"]

        # 低置信守卫：最高分低于阈值 = 库里确实没有 → 明确告知，
        # 防止弱模型拿近似片段编造口径（铁律 3：库没有 → 反问）。
        # miss 结果同样入缓存：反复查"库里没有"的问句是最高频的重复场景
        best = top[0][1] if top else 0.0
        if _guard(best, LOW_CONF_THRESHOLD) == "miss":
            out = (
                f"知识库中未找到高相关片段（最高相关分 {best:.2f}）。\n"
                "禁止编造口径：如涉及指标计算，请先向用户确认口径后再继续。"
            )
            redis_cache.set(cache_key, out, redis_cache.RAG_TTL)
            return out

        lines = ["知识库检索结果（按相关性排序，引用时标注文档与章节）："]
        for i, ((cid, _content), s) in enumerate(top, 1):
            row = id2row[cid]
            loc = " / ".join(row[2]) or "（无章节）"
            content = " ".join(row[1].split())[:200]
            lines.append(f"[{i}] {row[3]} | {loc}（相关分 {s:.2f}）\n    {content}")
        out = "\n".join(lines)
        redis_cache.set(cache_key, out, redis_cache.RAG_TTL)
        return out