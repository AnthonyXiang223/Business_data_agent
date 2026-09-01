"""评估结果报告（从 eval_retrieval.py 拆出，纯打印）。"""

from __future__ import annotations


def _pct(x) -> str:
    return "-" if x is None else f"{x:.0%}"


def print_summary(results: list[dict]) -> None:
    print("\n" + "=" * 100)
    print(f"{'题目':<18} {'通过率':<8} {'首轮':<8} {'自修复':<8} {'均轮':<6} {'judge':<6} 问题")
    print("=" * 100)
    total_pass = total_first = total_repair = 0
    n_first = n_repair = 0
    for r in results:
        m = r["metrics"]
        total_pass += m["pass_rate"]
        if m["first_shot"] is not None:
            total_first += m["first_shot"]
            n_first += 1
        if m["self_repair"] is not None:
            total_repair += m["self_repair"]
            n_repair += 1
        print(
            f"{r['case']['id']:<18} {_pct(m['pass_rate']):<8} {_pct(m['first_shot']):<8} "
            f"{_pct(m['self_repair']):<8} {m['avg_rounds']:<6.1f} {m['judge_calls']:<6} "
            f"{r['case']['question'][:32]}"
        )
        if m.get("tool_stats"):
            ts = m["tool_stats"]
            print(
                f"{'':<18}  └ 多轮成本: 画像重查 {ts['profile_recalls']:.1f} · "
                f"schema重查 {ts['schema_recalls']:.1f} · 口径重查 {ts['metric_recalls']:.1f} · "
                f"重复SQL {ts['duplicate_sql']:.1f} · 均工具调用 {ts['total_tool_calls']:.1f}"
            )
    print("=" * 100)
    n = len(results)
    print(f"总通过率: {total_pass / n:.0%}")
    if n_first:
        print(f"首轮正确率: {total_first / n_first:.0%}")
    if n_repair:
        print(f"护栏自修复率: {total_repair / n_repair:.0%}")
    total_judge = sum(r["metrics"]["judge_calls"] for r in results)
    print(f"judge 总调用数: {total_judge}")


def print_failures(results: list[dict]) -> None:
    """打印失败尝试的细节：判定途径、judge 理由与 issue_tags，方便归因。"""
    print("\n" + "-" * 100)
    print("失败尝试明细：")
    found = False
    for r in results:
        for i, a in enumerate(r["attempts"]):
            if a["passed"]:
                continue
            found = True
            v = a["verdict"]
            print(f"\n[{r['case']['id']}] 第{i + 1}次 · 轮数 {a['rounds']} · "
                  f"via={v.get('via')} · confidence={v.get('confidence')} · "
                  f"{'有护栏拦截' if a['blocked'] else '无拦截'}")
            print(f"  问题: {r['case']['question']}")
            if v.get("issue_tags"):
                print(f"  issue_tags: {v['issue_tags']}")
            print(f"  judge 理由: {v.get('reason', '')[:200]}")
            print(f"  最终答案: {a.get('final_text', '（断点续跑，无原文）')[:150]}")
            for sql, res in a.get("records", []):
                flag = " [被拦截]" if "[被护栏拦截]" in res else ""
                print(f"  SQL: {sql[:90]}{flag}")
    if not found:
        print("（全部通过）")
    print("-" * 100)
