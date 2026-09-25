"""评估运行层（从 eval_retrieval.py 拆出）：Agent 构建 + SQL 捕获 + 单次尝试 + 聚合。

Agent 在模块 import 时构建（与拆分前行为一致，--resume 全跳过也构建）。
multi 模式复用 multi_analyst 的主管 + 子智能体结构（惰性 import）。
"""

from __future__ import annotations

import copy
import os
import sys
import threading
import time
from pathlib import Path

# agent/（analyst_tools、model_guard、metrics、redis_cache）与 rag/（rag_tools）
# 不在 eval 目录：垫进 sys.path，CLI 运行不依赖 PYTHONPATH 手动设置
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "rag"))

from dotenv import load_dotenv
from langchain_core.messages import ToolMessage
from langchain_core.tracers.context import collect_runs
from langchain_openai import ChatOpenAI

from deepagents import create_deep_agent

from analyst_tools import (
    create_chart,
    get_data_profile,
    get_dataset_schema,
    get_metric_definitions,
    list_datasets,
    query_data,
)
from model_guard import _EmptyTurnRetryMiddleware
from stats_tools import (
    analyze_trend,
    compare_distribution,
    correlation_analysis,
    drill_down,
)
from rag_tools import search_knowledge
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
- analyze_trend: 时序趋势（按日/周/月聚合指标，可选环比/同比）
- drill_down: 多维下钻（1-2 个维度分组聚合 + 结构化过滤 + Top N）
- compare_distribution: 分布对比（分组统计量：四分位/均值/标准差并排比较）
- correlation_analysis: 相关性分析（数值列两两 Pearson 相关矩阵）
- search_knowledge: 检索业务知识库（指标口径文档/字段说明文档）的权威原文片段
- create_chart: 生成标准图表规格 JSON 供前端渲染（bar/line/pie/table）

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
7. 图表规范：仅在用户明确要求画图时才调用 create_chart；用户没要求时只输出文字
8. 知识库佐证：需要口径原文佐证时调用 search_knowledge（先用 get_metric_definitions
   归一指标名再检索）；检索片段只作口径依据，数值必须 query_data 现查
"""

_model = ChatOpenAI(
    model_name=os.environ.get("OPENAI_MODEL_NAME", "Qwen/Qwen3-8B"),
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ["OPENAI_API_BASE"],
)

_single_agent = create_deep_agent(
    model=_model,
    # 对齐生产单 agent 配置（analyst.py）：同款 11 工具 + 同款空轮护栏——
    # 评估必须测生产同款配置，空轮/修复计数才有意义
    middleware=[_EmptyTurnRetryMiddleware()],
    tools=[list_datasets, get_dataset_schema, get_data_profile,
           get_metric_definitions, query_data,
           analyze_trend, drill_down, compare_distribution, correlation_analysis,
           create_chart, search_knowledge],
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
    """从进程内 Run 树提取全部工具调用（含嵌套子智能体），供轨迹统计。

    与 _walk_cb_runs 同源但不过滤工具：轨迹评估要的不止 query_data，
    还有工具覆盖/违规判分与每步延迟（Run 树节点自带 start/end 时间戳，
    无需额外埋点）。
    """
    calls: list[dict] = []
    for r in runs:
        if r.run_type == "tool":
            latency_ms = None
            if getattr(r, "start_time", None) and getattr(r, "end_time", None):
                latency_ms = int((r.end_time - r.start_time).total_seconds() * 1000)
            calls.append({"name": r.name, "args": r.inputs or {},
                          "latency_ms": latency_ms})
        calls.extend(_walk_tool_calls(r.child_runs or []))
    return calls


def _llm_usage(run) -> tuple[int, int] | None:
    """单个 LLM run 的 (input_tokens, output_tokens)；取不到返回 None。

    优先 llm_output.token_usage（langchain-openai 每个 run 都带，含缓存细分，
    实测 deepseek 网关非流式响应确实回填）；兜底序列化消息的
    kwargs.usage_metadata（Run 树里消息是 lc/kwargs 序列化形态，不是
    model_dump——messages[..].data 路径不存在，这是实测踩出来的）。
    """
    outs = run.outputs or {}
    tu = (outs.get("llm_output") or {}).get("token_usage") or {}
    if tu:
        return (int(tu.get("prompt_tokens") or 0),
                int(tu.get("completion_tokens") or 0))
    try:
        gens = outs.get("generations") or []
        kwargs = gens[0][0].get("message", {}).get("kwargs") or {}
        usage = kwargs.get("usage_metadata") or {}
        if usage:
            return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    except Exception:
        pass
    return None


def _walk_llm_runs(runs: list) -> dict:
    """从进程内 Run 树提取 LLM 调用统计（含嵌套子智能体）：次数 / token / 延迟。

    usage_missing = 网关未回 usage 的调用数——全缺时报告层显示 '-' 而非
    错误的 0 均值（None-safe，不阻塞判分主线）。
    """
    stats = {"n_calls": 0, "input_tokens": 0, "output_tokens": 0,
             "latency_ms": 0, "usage_missing": 0}
    for r in runs:
        if r.run_type == "llm":
            stats["n_calls"] += 1
            tokens = _llm_usage(r)
            if tokens is None:
                stats["usage_missing"] += 1
            else:
                stats["input_tokens"] += tokens[0]
                stats["output_tokens"] += tokens[1]
            if getattr(r, "start_time", None) and getattr(r, "end_time", None):
                stats["latency_ms"] += int(
                    (r.end_time - r.start_time).total_seconds() * 1000)
        for k, v in _walk_llm_runs(r.child_runs or []).items():
            stats[k] += v
    return stats


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


def _run_once(agent, question: str, history: list[dict] | None = None,
              tag: str = "", case_id: str = "", mode: str = "") -> dict:
    """单次尝试：调用 agent，返回 (最终文本, 记录快照, 轨迹/效率数据, run_id)。

    采集三路（均自进程内 Run 树，含子智能体嵌套轨迹，无网络竞态）：
    - SQL 记录：_walk_cb_runs（query_data 的 sql+结果），无调用时回退消息轨迹
    - 轨迹：_walk_tool_calls（全部工具调用 名+参数+延迟）
    - 效率：_walk_llm_runs（LLM 次数/token/延迟）+ wall_seconds 墙钟
    agent.invoke 抛异常（如工具异常穿透）时降级为失败尝试，不中断整套评估。
    """
    reset_judge_calls()
    config: dict = {}
    if tag:
        # metadata 向下传播到整棵 run 树；run_name = LangSmith trace 名
        config["metadata"] = {
            "eval_attempt": tag,
            "question_id": case_id,
            "agent_mode": mode,
            "model": os.environ.get("OPENAI_MODEL_NAME", ""),
        }
        config["run_name"] = f"{tag} {question[:20]}"
    messages = _build_messages(question, history)
    t0 = time.perf_counter()
    with collect_runs() as cb:
        try:
            out = agent.invoke({"messages": messages}, config=config)
        except Exception as e:
            seq = _walk_tool_calls(cb.traced_runs)
            records = _walk_cb_runs(cb.traced_runs)
            return {
                "final_text": "",
                "records": records,
                "rounds": len(records),
                "blocked": False,
                "tool_stats": _tool_stats(seq),
                "tool_seq": seq,
                "llm_stats": _walk_llm_runs(cb.traced_runs),
                "wall_seconds": time.perf_counter() - t0,
                "error": f"{type(e).__name__}: {str(e)[:300]}",
                "run_id": _root_run_id(cb),
            }
        records = _walk_cb_runs(cb.traced_runs)
        tool_seq = _walk_tool_calls(cb.traced_runs)
        llm_stats = _walk_llm_runs(cb.traced_runs)
        run_id = _root_run_id(cb)
        wall_seconds = time.perf_counter() - t0
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
        "tool_stats": _tool_stats(tool_seq),
        "tool_seq": tool_seq,
        "llm_stats": llm_stats,
        "wall_seconds": wall_seconds,
        "run_id": run_id,
    }


# ---------------------------------------------------------------------------
# LangSmith 回写：判分结果挂钩 trace（correct / blocked），UI 可按分数过滤
# ---------------------------------------------------------------------------
def _root_run_id(cb) -> str | None:
    """根 run 的 id（= LangSmith trace id），供判分回写挂钩。

    RunCollectorCallbackHandler 继承 BaseTracer，按【完成】顺序收集
    （run 结束时才 persist）——traced_runs[0] 是最先完成的叶节点，
    根 run 最后完成。用 parent_run_id is None 定位根，不依赖顺序假设。
    """
    for r in reversed(cb.traced_runs):
        if getattr(r, "parent_run_id", None) is None:
            return str(r.id)
    return None


_langsmith_client = None


def _get_langsmith_client():
    global _langsmith_client
    if _langsmith_client is None:
        from langsmith import Client
        _langsmith_client = Client()
    return _langsmith_client


def _eval_session_id(client) -> str | None:
    """eval 项目的 id，作 run 级 feedback 的 session_id。

    SmithDB 迁移要求：run 级 feedback 必须带 session_id 才能定位 run
    （传统后端 run.session_id 即项目 id），否则仅告警、未来直接报错。
    一次解析进程内复用；失败返回 None 退化为旧行为（不中断评估）。
    """
    project_name = os.environ.get("LANGSMITH_PROJECT", "")
    if not project_name:
        return None
    try:
        return str(client.read_project(project_name=project_name).id)
    except Exception:
        return None


def _feedback_once(client, run_id: str, key: str, score: float, comment: str,
                   session_id: str | None) -> bool:
    """单条 feedback，带一次延迟重试（trace 上报异步，根 run 可能尚未落库）。"""
    for attempt in range(2):
        try:
            client.create_feedback(
                run_id,
                key=key,
                score=score,
                comment=comment,
                trace_id=run_id,       # 根 run id == trace id；带 trace_id 走后台批量上传
                session_id=session_id,
            )
            return True
        except Exception:
            if attempt == 0:
                time.sleep(2)
    return False


def write_feedback(case: dict, attempts: list[dict], mode: str) -> int:
    """判分回写 LangSmith：每题判完即写，返回成功条数（correct+blocked 各 1 条/尝试）。

    可观测性是副作用：未开 tracing、无 run_id（超时放弃）、LangSmith
    不可达都静默降级，绝不让评估主线失败。
    """
    if os.environ.get("LANGSMITH_TRACING", "").strip().lower() not in ("true", "1"):
        return 0
    client = _get_langsmith_client()
    session_id = _eval_session_id(client)
    written = 0
    for att in attempts:
        run_id = att.get("run_id")
        if not run_id:
            continue
        verdict = att.get("verdict", {})
        comment = f"{mode} via={verdict.get('via')} {str(verdict.get('reason', ''))[:200]}"
        if _feedback_once(client, run_id, "correct",
                          1.0 if att["passed"] else 0.0, comment, session_id):
            written += 1
        if _feedback_once(client, run_id, "blocked",
                          1.0 if att.get("blocked") else 0.0, "", session_id):
            written += 1
    return written


ATTEMPT_TIMEOUT = 300  # 单次尝试墙钟超时（秒）：防模型绕圈挂死整套评估

# 超时/异常降级尝试的兜底字段（轨迹数据随线程放弃而丢失——见 attempt_worker）
_EMPTY_EFFORT = {"tool_seq": [], "llm_stats": {}, "wall_seconds": None}


def _trajectory_score(case: dict, att: dict, mode: str = "") -> dict:
    """工具覆盖 + 违规（纯规则判分，零 judge 调用）。

    coverage = 期望工具被调用比例（expected ⊆ called 的软判——单轮题
    多余的 schema/profile 探索是"先探后问"的期望行为，不罚）；
    violations = 禁调工具被调用次数（负样本=全部工具，非图表题=create_chart）。
    """
    expected = list(case.get("expected_tools") or [])
    if mode == "multi" and "create_chart" in expected:
        # multi 模式没有 create_chart 工具（chart-spec 子智能体直接出 spec 文本）
        expected.remove("create_chart")
    forbidden = case.get("forbidden_tools") or []
    called = [c["name"] for c in att.get("tool_seq", [])]
    coverage = len(set(expected) & set(called)) / len(expected) if expected else 1.0
    violations = sum(called.count(t) for t in forbidden)
    return {"coverage": coverage, "violations": violations, "called": called}


def attempt_worker(case: dict, agent, run_idx: int, mode: str = "") -> tuple[str, dict]:
    """线程池工作单元：跑一次尝试并判定，返回 (case_id, attempt)。

    带墙钟超时：agent.invoke 在子线程执行，超时则放弃该尝试并记为失败。
    被放弃的线程由 daemon 兜底，不阻塞评估主流程。
    """
    tag = f"{case['id']}#{run_idx + 1}"
    result_box: dict = {}

    def _invoke():
        result_box["att"] = _run_once(
            agent, case["question"], history=case.get("history"),
            tag=tag, case_id=case["id"], mode=mode,
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
            **_EMPTY_EFFORT,
        }
    else:
        att = result_box.get("att") or {
            "final_text": "",
            "records": [],
            "rounds": 0,
            "blocked": False,
            "tool_stats": {},
            "error": "invoke 未返回结果",
            **_EMPTY_EFFORT,
        }
    att["trajectory"] = _trajectory_score(case, att, mode)
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
    """序列化尝试（去掉 records/final_text 长文本，保留 verdict 供失败明细展示）。

    轨迹只落 called 名单（tool_seq 的 args 不落盘）；llm_stats/wall_seconds
    全量。旧断点文件缺这些键 → aggregate 用 .get 兜底，可直接 --resume。
    """
    return {
        "passed": att["passed"],
        "verdict": att["verdict"],  # 仅含可序列化字段（passed/via/reason/confidence/issue_tags）
        "rounds": att["rounds"],
        "blocked": att["blocked"],
        "judge_calls": att["judge_calls"],
        "tool_stats": att.get("tool_stats", {}),
        "trajectory": att.get("trajectory", {}),
        "llm_stats": att.get("llm_stats", {}),
        "wall_seconds": att.get("wall_seconds"),
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

    # 轨迹 + 效率指标（attempt 级已序列化落盘；resume 旧文件缺键用 .get 兜底。
    # token 均值只取有 usage 的尝试——网关不回 usage 时显示 None 而非错误的 0）
    traj = [a.get("trajectory") or {} for a in attempts]
    coverages = [t.get("coverage") for t in traj if t.get("coverage") is not None]
    violations = sum(t.get("violations", 0) for t in traj)
    n_tools = [len(t.get("called", [])) for t in traj]
    llm = [a.get("llm_stats") or {} for a in attempts]
    n_llm = [s.get("n_calls", 0) for s in llm]
    in_tok = [s.get("input_tokens") for s in llm if s.get("input_tokens") is not None]
    out_tok = [s.get("output_tokens") for s in llm if s.get("output_tokens") is not None]
    walls = [a.get("wall_seconds") for a in attempts if a.get("wall_seconds") is not None]

    return {
        "pass_rate": len(passed) / len(attempts),
        "first_shot": first_shot,
        "self_repair": (len(repaired) / len(blocked_attempts)) if blocked_attempts else None,
        "avg_rounds": (sum(rounds) / len(rounds)) if rounds else 0.0,
        "judge_calls": judge_calls,
        "via": via,
        "issue_tags": tags,
        "tool_stats": tool_stats,
        "tool_coverage": (sum(coverages) / len(coverages)) if coverages else None,
        "violations": violations,
        "avg_tool_calls": (sum(n_tools) / len(n_tools)) if n_tools else 0.0,
        "avg_llm_calls": (sum(n_llm) / len(n_llm)) if n_llm else 0.0,
        "avg_input_tokens": (sum(in_tok) / len(in_tok)) if in_tok else None,
        "avg_output_tokens": (sum(out_tok) / len(out_tok)) if out_tok else None,
        "avg_wall_s": (sum(walls) / len(walls)) if walls else None,
    }
