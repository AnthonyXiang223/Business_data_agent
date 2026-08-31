"""业务数据分析 Agent（deepagents 单 Agent 版，阶段 1+2：取数 + 图表）。

运行（在 demo_agent 目录）:
    ../.venv/bin/python analyst.py "你的分析问题"
不带参数时会交互式提示输入问题（也可 echo "问题" | analyst.py 管道传入）。

同时由 langgraph.json 以 graph id "analyst" 挂载给 langgraph dev（前端连 127.0.0.1:2024）。
后续阶段路线见 CLAUDE.md。
"""

import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import json_repair

# langgraph dev 对 path 形式 graph 用 spec_from_file_location 加载，不会把本目录加进
# sys.path，裸 `from analyst_tools import ...` 会 ImportError；垫片同时兼容 CLI 运行
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage
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

load_dotenv()

model = ChatOpenAI(
    model_name=os.environ.get("OPENAI_MODEL_NAME", "Qwen/Qwen3-8B"),
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ["OPENAI_API_BASE"],
)

ANALYST_PROMPT = """你是一位严谨的业务数据分析师。

可用工具：
- list_datasets: 查看有哪些数据集
- get_dataset_schema: 了解列结构与业务含义
- get_data_profile: 数据质量画像（分析任何问题前必须先调用）
- query_data: 用 SQL 查询数据（表名 = 数据集名，duckdb 方言；分桶写法见工具说明）
- get_metric_definitions: 查询指标口径（销售额/GMV、客单价等如何计算的权威标准）
- create_chart: 生成标准图表规格 JSON 供前端渲染（bar/line/pie/table）

行为准则：
1. 歧义优先：问题有歧义（指标口径/统计范围/评价标准/分桶区间）先反问澄清，
   禁止替用户假设直接给结论；问题明确时直接执行，不要过度反问
2. 先探后问：先 get_data_profile 了解数据质量（缺失/重复/非法日期要报告），
   再按"探查 → 假设 → 验证 → 修正结论"推进分析
3. 口径权威：算指标先查 get_metric_definitions，公式以口径库为准，禁止自创；
   把用户原话作为 keyword 传入，工具负责匹配；计数/分桶/过滤类问题不查
4. 引用数据：结论必须引用工具返回的具体数值，面向业务人员给结论，不罗列原始数字
5. 图表规范：仅在用户明确要求画图/图表时才调用 create_chart，用户没要求时只输出文字分析；
   需要画图时，先调用 create_chart 生成图表，再输出文字总结；
   禁止在回答中输出图片链接、图表代码（DSL/mermaid/ASCII 图）——图表只能通过 create_chart 生成；
   规格严格符合契约 v1；校验失败按工具返回的错误信息修正后重试；
   数据未确认（口径/数值未核实）前不要生成图表

复杂分析时，把中间结论写入 /workspace 笔记文件，防止上下文丢失。
"""

# ---------- 畸形工具调用修复 + 空轮重试护栏 ----------

_EMPTY_TURN_NUDGE = (
    "（系统提示）你上一条回复没有成功发起工具调用（输出可能被截断）。"
    "请重新发起被截断的工具调用，只输出工具调用本身，不要写其他文字。"
)


def _repair_invalid_calls(message: AIMessage) -> AIMessage | None:
    """用 json-repair 修复畸形工具调用（截断/缺引号等，Qwen3-8B 经网关偶发）。
    全部调用修复成功才返回新消息（含修复后的 tool_calls），否则 None。"""
    if not message.invalid_tool_calls:
        return None
    repaired = []
    for i, tc in enumerate(message.invalid_tool_calls):
        raw = tc.get("args")
        if not isinstance(raw, str):
            return None
        try:
            fixed_args = json_repair.loads(raw)
        except Exception:
            return None
        if not isinstance(fixed_args, dict):
            return None
        repaired.append(
            {
                "name": tc.get("name"),
                "args": fixed_args,
                "id": tc.get("id") or f"repair-{i}",
                "type": "tool_call",
            }
        )
    return message.model_copy(update={"tool_calls": repaired, "invalid_tool_calls": []})


def _is_unusable_response(response: ModelResponse) -> bool:
    """模型这轮什么都没产出（无文本、无工具调用）→ 视为被截断的空轮。"""
    messages = response.result or []
    if not messages:
        return True
    last = messages[-1]
    if isinstance(last, AIMessage) and not last.text.strip() and not last.tool_calls:
        return True
    return False


def _repair_or_none(response: ModelResponse) -> ModelResponse | None:
    """畸形工具调用本地修复（零额外 LLM 调用）；修复成功返回替换响应，否则 None。"""
    for msg in response.result or []:
        if isinstance(msg, AIMessage) and msg.invalid_tool_calls and not msg.tool_calls:
            fixed = _repair_invalid_calls(msg)
            if fixed is not None:
                return ModelResponse(
                    result=[fixed],
                    structured_response=response.structured_response,
                )
            break
    return None


def _guard_model_call(request: ModelRequest, handler) -> ModelResponse:
    """同步守护：json-repair 修复 → 失败则 nudge 重试一次 → 再失败原样返回。"""
    response = handler(request)
    fixed = _repair_or_none(response)
    if fixed is not None:
        return fixed
    if _is_unusable_response(response):
        nudged = request.override(
            messages=[*request.messages, HumanMessage(content=_EMPTY_TURN_NUDGE)]
        )
        second = handler(nudged)
        if not _is_unusable_response(second):
            return second
    return response


async def _guard_async_model_call(request: ModelRequest, handler) -> ModelResponse:
    """异步守护：与 _guard_model_call 相同逻辑（langgraph dev 服务器走异步路径）。"""
    response = await handler(request)
    fixed = _repair_or_none(response)
    if fixed is not None:
        return fixed
    if _is_unusable_response(response):
        nudged = request.override(
            messages=[*request.messages, HumanMessage(content=_EMPTY_TURN_NUDGE)]
        )
        second = await handler(nudged)
        if not _is_unusable_response(second):
            return second
    return response


class _EmptyTurnRetryMiddleware(AgentMiddleware):
    """模型轮次护栏：Qwen3-8B 经网关偶发产出畸形/空工具调用
    （'finish_reason=tool_calls 但 tool_calls 为空'），agent 循环会静默结束。
    同步（CLI invoke）与异步（langgraph dev 服务器）都必须实现，
    只实现 wrap_model_call 会在异步上下文抛 NotImplementedError。"""

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        return _guard_model_call(request, handler)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await _guard_async_model_call(request, handler)


agent = create_deep_agent(
    model=model,
    middleware=[_EmptyTurnRetryMiddleware()],
    tools=[
        list_datasets,
        get_dataset_schema,
        get_data_profile,
        get_metric_definitions,
        query_data,
        create_chart,
    ],
    system_prompt=ANALYST_PROMPT,
)

if __name__ == "__main__":
    # 单轮问答。两种输入方式：
    #   1) 命令行参数: analyst.py "你的问题"
    #   2) 不带参数: 交互式提示输入（或管道传入，如 echo "问题" | analyst.py）
    if len(sys.argv) > 1:
        question = sys.argv[1]
    elif sys.stdin.isatty():
        question = input("请输入你的问题: ").strip()
    else:
        question = sys.stdin.read().strip()
    if not question:
        print('用法: ../.venv/bin/python analyst.py "你的问题"')
        sys.exit(1)
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    print(result["messages"][-1].content)
