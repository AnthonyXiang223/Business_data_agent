"""评估结果报告（从 eval_retrieval.py 拆出，纯打印）。

三个打印入口：print_summary（正确性 + 轨迹/效率每题聚合）、
print_failures（失败归因，含轨迹 vs 期望 diff）、
print_efficiency（进程级效率快照：空轮/修复/缓存命中率）。
"""

from __future__ import annotations


def _pct(x) -> str:
    return "-" if x is None else f"{x:.0%}"


def _rate(num: int, den: int) -> str:
    """进程级计数比率：分母为 0 → '-'（如 multi 模式无 middleware 计数）。"""
    return f"{num / den:.1%}" if den else "-"


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
        if m.get("tool_coverage") is not None:
            in_tok = "-" if m["avg_input_tokens"] is None else f"{m['avg_input_tokens']:.0f}"
            out_tok = "-" if m["avg_output_tokens"] is None else f"{m['avg_output_tokens']:.0f}"
            wall = "-" if m["avg_wall_s"] is None else f"{m['avg_wall_s']:.1f}s"
            print(
                f"{'':<18}  └ 轨迹: 工具覆盖 {_pct(m['tool_coverage']):<5} · "
                f"违规 {m['violations']} · 工具均 {m['avg_tool_calls']:.1f} · "
                f"LLM均 {m['avg_llm_calls']:.1f} · token均 {in_tok}+{out_tok} · "
                f"墙钟均 {wall}"
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
    # 轨迹汇总（纯规则判分，零 judge）
    covs = [m["tool_coverage"] for r in results
            if (m := r["metrics"]).get("tool_coverage") is not None]
    if covs:
        print(f"工具选择准确率: {sum(covs) / len(covs):.0%}")
    print(f"违规调用总数: {sum(r['metrics']['violations'] for r in results)}")
    in_toks = [m["avg_input_tokens"] for r in results
               if (m := r["metrics"]).get("avg_input_tokens")]
    out_toks = [m["avg_output_tokens"] for r in results
                if (m := r["metrics"]).get("avg_output_tokens")]
    walls = [m["avg_wall_s"] for r in results if (m := r["metrics"]).get("avg_wall_s")]
    if in_toks and out_toks:
        print(f"平均 token/题（单次尝试）: 入 {sum(in_toks) / len(in_toks):.0f} + "
              f"出 {sum(out_toks) / len(out_toks):.0f}")
    if walls:
        print(f"平均墙钟/题（单次尝试）: {sum(walls) / len(walls):.1f}s")


def print_efficiency(eff_stats: dict | None) -> None:
    """进程级效率快照：模型轮次护栏（空轮/修复）+ 缓存命中率。

    guard 计数来自 _EmptyTurnRetryMiddleware（multi 模式未挂 → 全 0 显示 '-'）；
    cache 计数来自 redis_cache（get/set 内部，按 key 前缀分桶）。
    """
    if not eff_stats:
        return
    guard = eff_stats.get("guard") or {}
    calls = guard.get("model_calls_total", 0)
    print("\n" + "-" * 100)
    print("效率快照（进程级累计）:")
    print(f"  模型调用轮次: {calls}")
    print(f"  空轮率: {_rate(guard.get('empty_turns', 0), calls)}"
          f"（{guard.get('empty_turns', 0)} 轮；nudge 重试 {guard.get('nudge_retries', 0)} 次，"
          f"挽回 {guard.get('nudge_recovered', 0)} 次）")
    repairs = guard.get("repaired_calls", 0) + guard.get("repair_failed", 0)
    print(f"  无效工具调用率: {_rate(guard.get('invalid_tool_calls', 0), calls)}"
          f"（{guard.get('invalid_tool_calls', 0)} 条；json-repair 修复 "
          f"{guard.get('repaired_calls', 0)} 条，失败 {guard.get('repair_failed', 0)} 条）")
    cache = eff_stats.get("cache") or {}
    for prefix, label in (("sql", "SQL"), ("rag", "RAG")):
        b = cache.get(prefix)
        if not b:
            continue
        hits, misses = b.get("hits", 0), b.get("misses", 0)
        total = hits + misses
        bypass = f" · 降级直查 {b.get('bypass', 0)}" if b.get("bypass") else ""
        print(f"  {label} 缓存命中率: {_rate(hits, total)}（{hits}/{total}，"
              f"写入 {b.get('writes', 0)} 次）{bypass}")
    print("-" * 100)


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
            # 轨迹 vs 期望 diff（断点续跑旧文件无轨迹 → 跳过）
            traj = a.get("trajectory") or {}
            called = traj.get("called")
            if called:
                expected = r["case"].get("expected_tools") or []
                print(f"  轨迹调用: {called}")
                missing = [t for t in expected if t not in called]
                if missing:
                    print(f"  期望未调用: {missing}")
    if not found:
        print("（全部通过）")
    print("-" * 100)
