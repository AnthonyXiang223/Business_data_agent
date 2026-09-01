"""评估运行层（从 eval_retrieval.py 拆出）：Agent 构建 + SQL 捕获 + 单次尝试 + 聚合。

Agent 在模块 import 时构建（与拆分前行为一致，--resume 全跳过也构建）。
multi 模式复用 multi_analyst 的主管 + 子智能体结构（惰性 import）。
"""

from __future__ import annotations

import copy
import os
import threading

from dotenv import load_dotenv
from langchain_core.messages import ToolMessage
from langchain_core.tracers.context import collect_runs
from langchain_openai import ChatOpenAI

from deepagents import create_deep_agent

from analyst_tools import (
    get_data_profile,
    get_dataset_schema,
    get_metric_definitions,
    list_datasets,
    query_data,
)
from eval_scoring import (
    get_judge_calls,
    judge_case,
    judge_first_shot,
    reset_judge_calls,
)

load_dotenv()

RETRIEVAL_PROMPT = """你是一位取数助手，负责把业务问题转化为正确的查询结果。

可用工具：
- list_datasets: 查看有哪些数据集
- get_dataset_schema: 了解列结构与业务含义
- get_data_profile: 数据质量画像
- get_metric_definitions: 查询指标口径（GMV/客单价等如何计算的权威标准）
- query_data: 用 SQL 查询数据（表名 = 数据集名，duckdb 方言；分桶写法见工具说明）

行为准则：
1. 歧义优先：问题有歧义（指标口径/统计范围/评价标准/分桶区间）先反问澄清，
   禁止替用户假设直接给结论；问题明确时直接执行，不要过度反问
2. 先探后问：查数前先用 get_dataset_schema 确认列名，必要时查 get_data_profile
3. 口径权威：算指标先查 get_metric_definitions，公式以口径库为准，禁止自创；
   把用户原话作为 keyword 传入，工具负责匹配；计数/分桶/过滤类问题不查
4. 字面取数：统计口径跟随问题字面，不要擅自去重/清洗；拿不准先反问
5. 迭代分析：SQL 被拦截或报错时，读懂错误信息，修正后重试；
   引用数据：最终答案给出关键数值，用一两句话说明即可
6. 只读：数据只能查询，任何写操作（清空/修改/删除）都不被允许
"""

_model = ChatOpenAI(
    model_name=os.environ.get("OPENAI_MODEL_NAME", "Qwen/Qwen3-8B"),
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ["OPENAI_API_BASE"],
)

_single_agent = create_deep_agent(
    model=_model,
    tools=[list_datasets, get_dataset_schema, get_data_profile,
           get_metric_definitions, query_data],
    system_prompt=RETRIEVAL_PROMPT,
)

_multi_agent = None


def _build_multi_agent():
    """多智能体模式：复用 multi_analyst 的主管 + 4 子智能体结构。
    SQL 捕获靠 LangSmith 嵌套 trace，子智能体工具无需任何包装。"""
    import multi_analyst

    return create_deep_agent(
        model=_model,
        tools=[],
        system_prompt=multi_analyst.SUPERVISOR_PROMPT,
        subagents=copy.deepcopy(multi_analyst.subagents),
    )


def get_agent(mode: str):
    global _multi_agent
    if mode == "single":
        return _single_agent
    if _multi_agent is None:
        _multi_agent = _build_multi_agent()
    return _multi_agent


# ---------------------------------------------------------------------------
# SQL 捕获：进程内 Run 树（collect_runs）+ 消息轨迹兜底
# ---------------------------------------------------------------------------
# 捕获方案演进史（每代都实测踩坑）：
# 1. thread-local 记录器：工具在另一线程执行 → 漏捕（"0 轮"假象）
# 2. LangSmith API 读 trace：上报异步，根 run 结束 ≠ 子 run 已落库 → 竞态漏捕
# 3. collect_runs 进程内 Run 树：与 LangSmith 同源（它上报的就是这棵树），
#    无网络、无竞态、含子智能体嵌套轨迹 → 当前方案
# LangSmith 保留给人工调试（CLI/UI），评估捕获不再依赖它。


def _walk_cb_runs(runs: list) -> list[tuple[str, str]]:
    """从 collect_runs 的进程内 Run 树提取 query_data 调用（含嵌套子智能体）。

    递归遍历 child_runs；tool 类型且名字匹配的 run，inputs 是工具参数
    （含 sql），outputs 是 ToolMessage（外层 dict 包一层 output）。
    """
    records: list[tuple[str, str]] = []
    for r in runs:
        if r.run_type == "tool" and r.name in ("query_data", "_logged_query_data"):
            inputs = r.inputs or {}
            result = r.outputs or ""
            if isinstance(result, dict):
                result = result.get("output", result)
            if hasattr(result, "content"):  # ToolMessage
                result = result.content
            records.append((str(inputs.get("sql", "")), str(result)))
        records.extend(_walk_cb_runs(r.child_runs or []))
    return records


def _extract_records(messages: list) -> list[tuple[str, str]]:
    """兜底：从消息轨迹提取 query_data 调用（trace 不可读时）。"""
    results = {
        m.tool_call_id: m.content
        for m in messages
        if isinstance(m, ToolMessage)
    }
    records = []
    for m in messages:
        for tc in getattr(m, "tool_calls", []) or []:
            if tc.get("name") in ("query_data", "_logged_query_data"):
                args = tc.get("args") or {}
                records.append((str(args.get("sql", "")), str(results.get(tc.get("id"), ""))))
    return records


# ---------------------------------------------------------------------------
# 运行与聚合
# ---------------------------------------------------------------------------

def _build_messages(question: str, history: list[dict] | None) -> list[dict]:
    """拼装输入消息：脚本化历史（user/assistant 交替）+ 本轮 question。

    history 是"理想的第一轮对话"，不真跑——所以本次 invoke 里捕获到的
    所有工具调用都属于最后一轮，first_shot/值包含判分无需适配。
    """
    messages: list[dict] = []
    for turn in history or []:
        messages.append({"role": "user", "content": turn["question"]})
        messages.append({"role": "assistant", "content": turn["answer"]})
    messages.append({"role": "user", "content": question})
    return messages


def _walk_tool_calls(runs: list) -> list[dict]:
    """从进程内 Run 树提取全部工具调用（含嵌套子智能体），供多轮成本统计。

    与 _walk_cb_runs 同源但不过滤工具：多轮评测要数的不止 query_data，
    还有 get_data_profile/get_dataset_schema/get_metric_definitions 的重查次数。
    """
    calls: list[dict] = []
    for r in runs:
        if r.run_type == "tool":
            calls.append({"name": r.name, "args": r.inputs or {}})
        calls.extend(_walk_tool_calls(r.child_runs or []))
    return calls


def _tool_stats(calls: list[dict]) -> dict:
    """多轮成本指标。

    profile/schema/口径重查 = 第二轮重复第一轮已完成的工作（第一轮的
    结论已在脚本化历史里）；重复 SQL = 同一查询执行多次的浪费。
    """
    names: dict[str, int] = {}
    for c in calls:
        names[c["name"]] = names.get(c["name"], 0) + 1
    sqls = [
        str(c["args"].get("sql"))
        for c in calls
        if c["name"] in ("query_data", "_logged_query_data") and c["args"].get("sql")
    ]
    return {
        "profile_recalls": names.get("get_data_profile", 0),
        "schema_recalls": names.get("get_dataset_schema", 0),
        "metric_recalls": names.get("get_metric_definitions", 0),
        "duplicate_sql": len(sqls) - len(set(sqls)),
        "total_tool_calls": len(calls),
    }


def _run_once(agent, question: str, history: list[dict] | None = None, tag: str = "") -> dict:
    """单次尝试：调用 agent，返回 (最终文本, 记录快照, 各类标志)。

    SQL 捕获：collect_runs 进程内 Run 树（含子智能体嵌套轨迹），
    无调用时回退消息轨迹提取。
    agent.invoke 抛异常（如工具异常穿透）时降级为失败尝试，不中断整套评估。
    """
    reset_judge_calls()
    config: dict = {}
    if tag:
        config["metadata"] = {"eval_attempt": tag}  # LangSmith UI 可辨识的尝试标签
    messages = _build_messages(question, history)
    with collect_runs() as cb:
        try:
            out = agent.invoke({"messages": messages}, config=config)
        except Exception as e:
            records = _walk_cb_runs(cb.traced_runs)
            return {
                "final_text": "",
                "records": records,
                "rounds": len(records),
                "blocked": False,
                "tool_stats": _tool_stats(_walk_tool_calls(cb.traced_runs)),
                "error": f"{type(e).__name__}: {str(e)[:300]}",
            }
        records = _walk_cb_runs(cb.traced_runs)
        tool_calls = _walk_tool_calls(cb.traced_runs)
    final_text = out["messages"][-1].content
    snapshot = records
    if not snapshot:
        snapshot = _extract_records(out["messages"])
    blocked = any("[被护栏拦截]" in r for _, r in snapshot)
    return {
        "final_text": final_text,
        "records": snapshot,
        "rounds": len(snapshot),
        "blocked": blocked,
        "tool_stats": _tool_stats(tool_calls),
    }


ATTEMPT_TIMEOUT = 300  # 单次尝试墙钟超时（秒）：防模型绕圈挂死整套评估


def attempt_worker(case: dict, agent, run_idx: int) -> tuple[str, dict]:
    """线程池工作单元：跑一次尝试并判定，返回 (case_id, attempt)。

    带墙钟超时：agent.invoke 在子线程执行，超时则放弃该尝试并记为失败。
    被放弃的线程由 daemon 兜底，不阻塞评估主流程。
    """
    tag = f"{case['id']}#{run_idx + 1}"
    result_box: dict = {}

    def _invoke():
        result_box["att"] = _run_once(
            agent, case["question"], history=case.get("history"), tag=tag
        )

    t = threading.Thread(target=_invoke, daemon=True)
    t.start()
    t.join(timeout=ATTEMPT_TIMEOUT)
    if t.is_alive():
        att = {
            "final_text": "",
            "records": [],
            "rounds": 0,
            "blocked": False,
            "tool_stats": {},
            "error": f"尝试超时（>{ATTEMPT_TIMEOUT}s，疑似模型绕圈），已放弃",
        }
    else:
        att = result_box.get("att") or {
            "final_text": "",
            "records": [],
            "rounds": 0,
            "blocked": False,
            "tool_stats": {},
            "error": "invoke 未返回结果",
        }
    # judge 在本线程执行，先归零本线程计数（invoke 在子线程跑，计数不共享）
    reset_judge_calls()
    if att.get("error"):
        att["verdict"] = {
            "passed": False, "via": "error", "reason": att["error"], "confidence": 0.0,
        }
    else:
        att["verdict"] = judge_case(case, att["final_text"], att["records"])
    att["passed"] = att["verdict"]["passed"]
    att["judge_calls"] = get_judge_calls()
    return case["id"], att


def serialize_attempt(att: dict) -> dict:
    """序列化尝试（去掉 records/final_text 长文本，保留 verdict 供失败明细展示）。"""
    return {
        "passed": att["passed"],
        "verdict": att["verdict"],  # 仅含可序列化字段（passed/via/reason/confidence/issue_tags）
        "rounds": att["rounds"],
        "blocked": att["blocked"],
        "judge_calls": att["judge_calls"],
        "tool_stats": att.get("tool_stats", {}),
    }


def aggregate(case: dict, attempts: list[dict], judge_calls: int) -> dict:
    passed = [a for a in attempts if a["passed"]]
    blocked_attempts = [a for a in attempts if a["blocked"]]
    repaired = [a for a in blocked_attempts if a["passed"]]
    rounds = [a["rounds"] for a in attempts if a["rounds"] > 0]

    if case["type"] == "sql":
        first_shot = sum(
            1 for a in attempts
            if a["records"] and judge_first_shot(a["records"][0][0], case)
        ) / len(attempts)
    else:
        first_shot = None

    via = {}
    for a in attempts:
        v = a["verdict"].get("via")
        via[v] = via.get(v, 0) + 1
    tags = {}
    for a in attempts:
        if not a["passed"]:
            for t in a["verdict"].get("issue_tags", []):
                tags[t] = tags.get(t, 0) + 1

    # 多轮成本指标（仅 follow-up 题：单轮题的画像/口径调用是期望行为，不是浪费）
    tool_stats = None
    if case.get("history"):
        keys = ("profile_recalls", "schema_recalls", "metric_recalls",
                "duplicate_sql", "total_tool_calls")
        tool_stats = {
            k: sum(a.get("tool_stats", {}).get(k, 0) for a in attempts) / len(attempts)
            for k in keys
        }

    return {
        "pass_rate": len(passed) / len(attempts),
        "first_shot": first_shot,
        "self_repair": (len(repaired) / len(blocked_attempts)) if blocked_attempts else None,
        "avg_rounds": (sum(rounds) / len(rounds)) if rounds else 0.0,
        "judge_calls": judge_calls,
        "via": via,
        "issue_tags": tags,
        "tool_stats": tool_stats,
    }
