"""取数/行为能力评估集 v2（借鉴 QueryMind：执行产物 + LLM judge + 断点续跑）。

评估对象：deepagents 取数链路在京东数据集（12 中文字段）上能否把业务问题
转化为正确的查询结果；行为题（歧义/护栏）是否符合期望行为。

判定分层：
- sql 题：1) 免费直通——Agent 执行过的某条 SQL 结果值集合包含 golden 全部值
          → 通过（不花 judge 调用）
          2) LLM judge——直通失败时，把 golden 与 Agent 每个执行产物的摘要
          喂给 judge（能识别 partial/wrong_granularity 等近距失败）
          3) judge 解析失败 → 回退直通结果（失败）
- 行为题（clarify/clarify_small/negative）：LLM judge 判定，解析失败回退关键词

并行执行：题与题、尝试与尝试彼此独立，用线程池并行（--workers，默认 4）。
SQL 捕获：collect_runs 进程内 Run 树（multi 模式子智能体的嵌套轨迹也可见），
无调用时回退消息轨迹提取；judge 计数用 thread-local 隔离。
注意：并行度上限取决于 LLM 网关并发额度，遇到 429 频发就调低 --workers。

断点续跑：状态文件 hash = 题集内容 + agent 模式 + judge 模型配置（改了题
或配置旧状态自动失效）；--resume 复用已完成题目的结果。

模块划分：eval_dataset（题目加载）/ eval_scoring（判分）/ eval_runner（运行
聚合）/ eval_state（断点状态）/ eval_report（报告）；本文件仅保留 CLI 编排。

运行（在 demo_agent 目录）:
  python eval_retrieval.py --agent single --runs 3 --workers 4
  python eval_retrieval.py --agent multi  --runs 1
  python eval_retrieval.py --only gmv,count_records
  python eval_retrieval.py --resume
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

from eval_dataset import load_cases
from eval_report import print_failures, print_summary
from eval_runner import aggregate, attempt_worker, get_agent, serialize_attempt
from eval_state import load_state, save_state, state_path

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="取数/行为能力评估集 v2")
    parser.add_argument("--runs", type=int, default=3, help="每题运行次数（默认 3）")
    parser.add_argument("--only", type=str, default="", help="只跑指定题目 id（逗号分隔）")
    parser.add_argument("--agent", choices=["single", "multi"], default="single",
                        help="single = 单 Agent；multi = 主管+子智能体（阶段 4 基线）")
    parser.add_argument("--workers", type=int, default=4,
                        help="并行线程数（默认 4；网关 429 频发就调低）")
    parser.add_argument("--resume", action="store_true", help="断点续跑（跳过已完成题目）")
    args = parser.parse_args()

    cases = load_cases()
    if args.only:
        ids = {x.strip() for x in args.only.split(",")}
        cases = [c for c in cases if c["id"] in ids]
        if not cases:
            print(f"未找到题目: {args.only}")
            sys.exit(1)

    print(f"评估集 v2：{len(cases)} 题 × {args.runs} 次，agent 模式 = {args.agent}，"
          f"workers = {args.workers}，judge 模型 = "
          f"{os.environ.get('EVAL_JUDGE_MODEL', os.environ.get('OPENAI_MODEL_NAME'))}")
    agent = get_agent(args.agent)
    path = state_path(cases, args.agent)
    state = load_state(path) if args.resume else {"results": {}}

    # 并行调度：全部 (题目, 尝试) 任务扁平化进线程池
    by_case: dict[str, list[dict]] = defaultdict(list)
    ready: dict[str, dict] = {}  # case_id -> result（完成即入）
    todo = []
    for case in cases:
        saved = state.get("results", {}).get(case["id"])
        if saved and len(saved.get("attempts", [])) == args.runs:
            ready[case["id"]] = {
                "case": case,
                "attempts": saved["attempts"],
                "metrics": saved["metrics"],
            }
            print(f"[复用断点] {case['id']}", flush=True)
        else:
            todo.extend((case, agent, i) for i in range(args.runs))

    done_cases = len(ready)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(attempt_worker, case, ag, i): case["id"] for case, ag, i in todo}
        for fut in as_completed(futures):
            case_id, att = fut.result()
            by_case[case_id].append(att)
            case = next(c for c in cases if c["id"] == case_id)
            if len(by_case[case_id]) == args.runs:
                attempts = by_case[case_id]
                metrics = aggregate(case, attempts, sum(a["judge_calls"] for a in attempts))
                ready[case_id] = {"case": case, "attempts": attempts, "metrics": metrics}
                state["results"][case_id] = {
                    "attempts": [serialize_attempt(a) for a in attempts],
                    "metrics": metrics,
                }
                save_state(path, state)
                done_cases += 1
                print(f"[{done_cases}/{len(cases)}] {case_id} 完成，"
                      f"通过率 {metrics['pass_rate']:.0%}，均轮 {metrics['avg_rounds']:.1f}，"
                      f"判定途径 {metrics['via']}",
                      flush=True)

    results = [ready[c["id"]] for c in cases]
    print_summary(results)
    print_failures(results)


if __name__ == "__main__":
    main()
