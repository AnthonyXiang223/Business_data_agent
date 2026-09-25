"""FastAPI 服务层：会话管理 + SSE 流式输出 + 审计日志。
启动：
    .venv/bin/python -m uvicorn server.main:app --port 8000

事件协议见 server/sse.py；审计见 server/audit.py
"""

import asyncio
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

# 垫片：保证任意 CWD 下都能 import 项目根包（agent.analyst / server.*）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os

from dotenv import load_dotenv

load_dotenv()  # 必须先于 import agent（analyst 在 import 时读环境变量构建 graph）

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk, ToolMessage

from agent.analyst import agent  # 此 import 会把 agent 目录插入 sys.path（analyst.py 内置垫片）
import stop_guard  # 必须与 analyst.py 的中间件同一模块身份（顶层模块）。
# 若用 from agent import stop_guard 会注册第二个模块对象：两份独立的 _state
# 注册表，端点置的取消标志中间件永远读不到（模块双重身份陷阱，实测踩过）
from server import audit, sessions, sse
from server.schemas import ChatRequest

# LLM 长考（工具执行/模型思考）超过该时长无输出则发心跳注释行，防代理静默断流
_HEARTBEAT_SECONDS = 15.0


class _PumpError(Exception):
    """包装 pump 线程内的异常，跨线程传回事件循环后重新抛出。"""

    def __init__(self, error: BaseException):
        self.error = error


@asynccontextmanager
async def lifespan(_app: FastAPI):
    audit.init(os.environ["POSTGRES_URL"])
    yield
    audit.close()


app = FastAPI(title="业务数据分析 Agent 服务层", version="0.1.0", lifespan=lifespan)

# 中间件顺序：后 add 的在外层。审计在最内层（记录所有请求），CORS 在外层
app.add_middleware(audit.AuditMiddleware)   # 审计中间件
app.add_middleware(
    CORSMiddleware, # 跨域中间件
    allow_origins=["http://localhost:5175", "http://127.0.0.1:5175"],
    allow_methods=["*"],    # 允许的HTTP请求方法，*代表全部(GET POST PUT DELETE...)
    allow_headers=["*"],    # 允许前端携带的请求头
)


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    """SSE 流式对话。thread_id 缺省 = 新建会话，由首个 session 事件回传"""
    thread_id = req.thread_id or str(uuid.uuid4())
    config = {
        "configurable": {"thread_id": thread_id},
        # 首问进 checkpoint metadata（列表页标题）；沿用 CLI 的约定
        "metadata": {"title": req.message[:30]},
        "run_name": req.message[:30],  # LangSmith trace 名
    }
    # 记下已有消息 id：stream_mode="messages" 会把 checkpoint 里的历史消息
    # 重放一遍，用 id 过滤掉，只推本轮新增内容
    # PostgresSaver 仅同步接口，经线程池调同步 get_state
    prior = await asyncio.to_thread(agent.get_state, config)    # 丢线程池里跑

    # prior拿StateSnapshot对象，prio.values拿到完整的state dict，prior.values["messages"]拿到消息列表
    prior_ids = {m.id for m in (prior.values or {}).get("messages", [])}

    audit_fields = request.state.audit
    audit_fields.update(thread_id=thread_id, input_chars=len(req.message), run_state="ok")
    stats = {"tool_calls": 0, "output_chars": 0, "tokens": None}

    async def event_stream():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()  # 协程安全队列，用于协程之间传递消息
        _END = object() # 流结束哨兵
        finished = False  # 是否已发出收尾事件（done/stopped/error）；断开时据此置取消标志

        # ---- 思考过程过滤：模型在工具调用消息里夹带的叙事文本（"I'll start by..."）
        # 不进前端。AIMessageChunk 按消息 id 缓冲，消息结束时才可判定其性质：
        # 携带工具调用 → 丢弃缓冲（思考过程）；不携带 → 是最终回答，整体下发。
        # 代价：最终回答在流结束时一次性到达（文本先于 tool_call_chunks 流式到达，
        # 无法预知中途是否转为工具调用，只能整条缓冲）
        msg_id: str | None = None
        msg_has_tools = False
        msg_text: list[str] = []

        def _flush_msg_text():
            nonlocal msg_id, msg_has_tools, msg_text
            if msg_text and not msg_has_tools:
                text = "".join(msg_text)
                stats["output_chars"] += len(text)
                yield sse.encode("message", {"delta": text})
            msg_id, msg_has_tools, msg_text = None, False, []

        def _pump_sync():
            # 同步 graph 跑在工作线程（PostgresSaver 只有同步接口），
            # 每块经 call_soon_threadsafe 投递回事件循环。
            # 线程无法强杀：停止靠 CancellationMiddleware 在下一个模型调用处
            # 抛 RunCancelled（协作式），当前工具执行中不能打断、须等返回
            try:
                for chunk, _meta in agent.stream(
                    {"messages": [{"role": "user", "content": req.message}]},
                    config=config,
                    stream_mode="messages",
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, _PumpError(e))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _END)

        pump: asyncio.Task | None = asyncio.create_task(asyncio.to_thread(_pump_sync))
        # 新运行开始：清残留取消标志。上一轮被停止且立刻重发同 thread 时，
        # 旧工作线程尚在跑——标志是 thread 级的，不清会误杀新轮
        stop_guard.start_run(thread_id)

        try:
            yield sse.encode("session", {"thread_id": thread_id, "is_new": not prior_ids})
            while True:
                # 心跳与取块竞速：超时取消的只是 queue.get()（asyncio.Queue
                # 取消无害），pump 线程继续跑。不能用 asyncio.wait_for 直接
                # 包生成器的 anext()——超时取消会把生成器一起打断
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield sse.ping()    # 发一个SSE注释行表示还活着，别挂
                    continue
                if item is _END:
                    break
                if isinstance(item, _PumpError):
                    if isinstance(item.error, stop_guard.RunCancelled):
                        # 用户手动停止：发 stopped 事件（非 error），跳过 done
                        audit_fields["run_state"] = "stopped"
                        yield sse.encode(
                            "stopped",
                            {
                                "thread_id": thread_id,
                                "tool_calls": stats["tool_calls"],
                                "tokens": stats["tokens"],
                            },
                        )
                        finished = True
                        break
                    raise item.error
                chunk = item
                if getattr(chunk, "id", None) in prior_ids:
                    continue  # checkpoint 重放的历史消息，不推给客户端
                if isinstance(chunk, AIMessageChunk):
                    cid = getattr(chunk, "id", None)
                    if cid is not None and cid != msg_id:
                        # 上一条消息流完：无工具调用 → 最终回答下发；有 → 丢弃
                        for _ev in _flush_msg_text():
                            yield _ev
                        msg_id = cid
                    if getattr(chunk, "tool_call_chunks", None):
                        msg_has_tools = True  # 本条消息携带工具调用 → 其文本是思考过程
                    text = sessions._content_str(chunk)
                    if text:
                        msg_text.append(text)
                    for tc in chunk.tool_call_chunks or []:
                        # 只报每个工具调用的首块（携带 name），参数增量不推
                        if tc.get("name"):
                            stats["tool_calls"] += 1
                            yield sse.encode("tool_start", {"name": tc["name"]})
                    usage = getattr(chunk, "usage_metadata", None)
                    if usage and usage.get("total_tokens"):
                        stats["tokens"] = usage["total_tokens"]
                elif isinstance(chunk, ToolMessage):
                    # 触发工具的消息必有工具调用：其缓冲文本是思考过程，丢弃
                    for _ev in _flush_msg_text():
                        yield _ev
                    content = sessions._content_str(chunk)
                    if chunk.name == "create_chart":
                        # 图表规格 JSON 是前端渲染 Figure 的唯一数据源，必须完整下发
                        #（其余工具保持 200 字符截断：生产界面工具过程全隐的约定）
                        yield sse.encode(
                            "tool_end",
                            {
                                "name": chunk.name,
                                "content": content,
                                "tool_call_id": chunk.tool_call_id,  # 前端图卡稳定 key
                            },
                        )
                    else:
                        yield sse.encode(
                            "tool_end",
                            {"name": chunk.name, "preview": content[:200]},
                        )
            if not finished:
                # 最终回答：最后一条无工具调用的消息文本，流结束前整体下发
                for _ev in _flush_msg_text():
                    yield _ev
                yield sse.encode(
                    "done",
                    {"thread_id": thread_id, "tool_calls": stats["tool_calls"], "tokens": stats["tokens"]},
                )
                finished = True
        except Exception as e:  # agent 内部异常（LLM 网关/工具）→ 错误事件而非裸断流
            audit_fields["run_state"] = "error"
            finished = True
            yield sse.encode("error", {"detail": str(e)})
        finally:
            if not finished:
                # 客户端断开（GeneratorExit/CancelledError 都走 finally）：
                # 线程无法强杀，置取消标志让 graph 在下一个模型调用处自行
                # 结束，不再烧 token（此前是跑完为止）
                stop_guard.cancel(thread_id)
            if pump is not None:
                pump.cancel()
            audit_fields.update(
                output_chars=stats["output_chars"],
                tool_calls=stats["tool_calls"],
                tokens=stats["tokens"],
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx 反代时禁用缓冲，否则 SSE 不流
        },
    )


@app.get("/sessions")
async def list_sessions():
    return {"sessions": await sessions.list_sessions()}


@app.get("/sessions/{thread_id}/history")
async def get_history(thread_id: str):
    if not await sessions.thread_exists(thread_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"thread_id": thread_id, "messages": await sessions.get_history(thread_id)}


@app.post("/sessions/{thread_id}/stop")
async def stop_session(thread_id: str):
    """请求停止某会话的进行中运行（协作式取消，见 agent/stop_guard.py）。

    best-effort：不校验会话是否存在；标志在下一个模型调用处生效，
    无进行中运行时置位无害（该 thread 下次运行前被 start_run 清除）。
    """
    stop_guard.cancel(thread_id)
    return {"stopped": thread_id}


@app.delete("/sessions/{thread_id}")
async def delete_session(thread_id: str):
    if not await sessions.thread_exists(thread_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    await sessions.delete_session(thread_id)
    return {"deleted": thread_id}


@app.get("/audit")
async def list_audit(
    limit: int = Query(default=50, ge=1, le=500),
    thread_id: str | None = None,
):
    return {"logs": await audit.list_rows(limit=limit, thread_id=thread_id)}
