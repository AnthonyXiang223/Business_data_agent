"""模型轮次护栏（空轮重试 + json-repair 修复）：自 analyst.py 抽出的独立模块。

为什么独立：生产（analyst.py）与评估（eval/eval_runner.py 的 single agent）
都要挂同一套护栏——评估必须测生产同款配置，否则评估基线不代表生产。
analyst.py 模块级会构建 checkpointer 与 agent（PG 连接池等重副作用），
eval 直接 import analyst 会连带拉起整个生产环境；护栏独立后 eval 只
import 本模块，零副作用、单一来源。

处理顺序（每次模型调用）：handler → _repair_or_none（json-repair 本地修复，
零额外 LLM 调用）→ 空轮则 nudge 重试一次 → 再失败原样返回。
同步（CLI invoke）与异步（langgraph dev 服务器）双实现，只实现同步版
在异步上下文抛 NotImplementedError。

计数埋点走 metrics.py（eval 与生产共用同一份计数器）。
"""

import json_repair
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage

from metrics import bump

_EMPTY_TURN_NUDGE = (
    "（系统提示）你上一条回复没有成功发起工具调用（输出可能被截断）。"
    "请重新发起被截断的工具调用，只输出工具调用本身，不要写其他文字。"
)


def _repair_invalid_calls(message: AIMessage) -> AIMessage | None:
    """用 json-repair 修复畸形工具调用（截断/缺引号等，弱模型经网关偶发）。
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
    """畸形工具调用本地修复（零额外 LLM 调用）；修复成功返回替换响应，否则 None。

    计数埋点（agent/metrics.py，eval 与生产共用）：invalid_tool_calls 条数、
    修复成功/失败次数——无效工具调用率与修复率的分母来自 _guard_model_call 入口。
    """
    for msg in response.result or []:
        if isinstance(msg, AIMessage) and msg.invalid_tool_calls and not msg.tool_calls:
            bump("invalid_tool_calls", len(msg.invalid_tool_calls))
            fixed = _repair_invalid_calls(msg)
            if fixed is not None:
                bump("repaired_calls")
                return ModelResponse(
                    result=[fixed],
                    structured_response=response.structured_response,
                )
            bump("repair_failed")
            break
    return None


def _guard_model_call(request: ModelRequest, handler) -> ModelResponse:
    """同步守护：json-repair 修复 → 失败则 nudge 重试一次 → 再失败原样返回。"""
    bump("model_calls_total")
    response = handler(request)
    fixed = _repair_or_none(response)
    if fixed is not None:
        return fixed
    if _is_unusable_response(response):
        bump("empty_turns")
        nudged = request.override(
            messages=[*request.messages, HumanMessage(content=_EMPTY_TURN_NUDGE)]
        )
        bump("nudge_retries")
        second = handler(nudged)
        if not _is_unusable_response(second):
            bump("nudge_recovered")
            return second
    return response


async def _guard_async_model_call(request: ModelRequest, handler) -> ModelResponse:
    """异步守护：与 _guard_model_call 相同逻辑（langgraph dev 服务器走异步路径）。"""
    bump("model_calls_total")
    response = await handler(request)
    fixed = _repair_or_none(response)
    if fixed is not None:
        return fixed
    if _is_unusable_response(response):
        bump("empty_turns")
        nudged = request.override(
            messages=[*request.messages, HumanMessage(content=_EMPTY_TURN_NUDGE)]
        )
        bump("nudge_retries")
        second = await handler(nudged)
        if not _is_unusable_response(second):
            bump("nudge_recovered")
            return second
    return response


class _EmptyTurnRetryMiddleware(AgentMiddleware):
    """模型轮次护栏：弱模型经网关偶发产出畸形/空工具调用
    （'finish_reason=tool_calls 但 tool_calls 为空'），agent 循环会静默结束。"""

    def wrap_model_call(self, request: ModelRequest, handler) -> ModelResponse:
        return _guard_model_call(request, handler)

    async def awrap_model_call(self, request: ModelRequest, handler) -> ModelResponse:
        return await _guard_async_model_call(request, handler)
