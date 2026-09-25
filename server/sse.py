"""SSE 事件协议编码。

事件名固定、data 为 JSON，前端按 event 分派：
- session     会话信息（thread_id + 是否新建），流的第一个事件
- message     最终回答文本 {delta}（思考过程过滤：工具调用消息的叙事文本不下发，
              最终回答在流结束时整体到达）
- tool_end    工具返回 {name, preview}（preview 截断 200 字符）
- done        正常结束 {thread_id, tool_calls, tokens}
- stopped     用户手动停止 {thread_id, tool_calls, tokens}（截至停止的累计值）
- error       异常 {detail}

心跳用注释行（SSE 规范：':' 开头的行客户端必须忽略），
LLM 长考超过 15s 时发出，防代理/网关静默断流。
"""

import json


def encode(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def ping() -> str:
    return ": ping\n\n"
