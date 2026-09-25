"""会话管理：列表 / 历史 / 删除。

基于 agent.analyst 的 checkpointer（PG checkpoints 表）。
标题复用 CLI 约定：首问进 config metadata.title，存在 checkpoints 表
metadata 列；列表取每个 thread 第一条 checkpoint 的 metadata。
"""

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.analyst import agent, checkpointer

# 每个 thread 只取最早一条 checkpoint（metadata 里是首问标题）
_LIST_SQL = """
SELECT DISTINCT ON (thread_id) thread_id, metadata->>'title' AS title
FROM checkpoints ORDER BY thread_id, checkpoint_id ASC
"""


def _content_str(msg) -> str:
    """消息内容归一成 str：兼容纯文本与 content blocks 两种形态。"""
    content = msg.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                else:
                    parts.append(str(block))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def _serialize(msg) -> dict:
    """消息 → 前端友好 JSON（省掉内部字段）。"""
    if isinstance(msg, HumanMessage):
        return {"role": "user", "type": "text", "id": msg.id, "content": _content_str(msg)}
    if isinstance(msg, AIMessage):
        tool_calls = (
            [{"name": tc["name"], "args": tc.get("args")} for tc in msg.tool_calls]
            if msg.tool_calls
            else None
        )
        return {
            "role": "assistant",
            "type": "text",
            "id": msg.id,
            # 工具调用消息的文本是模型思考过程（叙事），历史恢复同样隐藏
            #（与 /chat 流式过滤口径一致；最终回答消息无 tool_calls，正常下发）
            "content": _content_str(msg) if not tool_calls else "",
            "tool_calls": tool_calls,
        }
    if isinstance(msg, ToolMessage):
        return {
            "role": "tool",
            "type": "tool",
            "id": msg.id,
            "name": msg.name,
            "content": _content_str(msg),
        }
    # SummarizationMiddleware 的摘要消息等其余类型，降级输出
    return {"role": "other", "id": msg.id, "content": _content_str(msg)}


def _list_rows() -> list[tuple[str, str]]:
    with checkpointer.conn.connection() as conn:
        rows = conn.execute(_LIST_SQL).fetchall()
    # 池配了 dict_row，返回的是 dict 不是 tuple，必须按键取值
    return [(r["thread_id"], r["title"]) for r in rows]


async def thread_exists(thread_id: str) -> bool:
    """会话是否存在（get_tuple 查得到 checkpoint 行才算存在）。

    不能用 get_state().values 判空：会话不存在时 values 是 {}（不是 None），
    空 dict 与"存在但没消息"无法区分。
    """
    tup = await asyncio.to_thread(
        checkpointer.get_tuple, {"configurable": {"thread_id": thread_id}}
    )
    return tup is not None


async def list_sessions() -> list[dict]:
    """全部会话：thread_id + 标题 + 消息数 + 最近更新时间，按时间倒序。"""
    rows = await asyncio.to_thread(_list_rows)
    out = []
    for tid, title in rows:
        # 消息数/更新时间必须走 agent.get_state：checkpoint 是增量链
        # （channels_from_checkpoint 沿父链合并），单行 channel_values
        # 只有最近一次增量（__pregel_tasks），看不到完整消息
        # PostgresSaver 仅同步接口，经线程池调同步方法
        state = await asyncio.to_thread(
            agent.get_state, {"configurable": {"thread_id": tid}}
        )
        messages = state.values.get("messages", [])
        out.append(
            {
                "thread_id": tid,
                "title": title or "(无标题)",
                "message_count": len(messages),
                "last_updated": state.created_at,
            }
        )
    out.sort(key=lambda s: s["last_updated"] or "", reverse=True)
    return out


async def get_history(thread_id: str) -> list[dict]:
    """某会话的完整消息历史（调用方先 thread_exists 判 404）。"""
    state = await asyncio.to_thread(
        agent.get_state, {"configurable": {"thread_id": thread_id}}
    )
    return [_serialize(m) for m in state.values.get("messages", [])]


async def delete_session(thread_id: str) -> None:
    """删除会话（checkpoints + checkpoint_writes）。"""
    await asyncio.to_thread(checkpointer.delete_thread, thread_id)
