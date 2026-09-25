"""运行中止护栏：跨线程取消标志 + 模型调用级拦截（协作式取消）。

为什么独立成模块：与 model_guard.py 同理——analyst.py 模块级构建
checkpointer/agent（PG 连接池等重副作用），stop 端点与测试只需 import
本模块，零副作用。

取消是协作式的：同步 graph 跑在工作线程，线程无法强杀（CLAUDE.md
铁律外已记录的边界）。置位标志后 graph 在**下一次模型调用**抛
RunCancelled 结束——当前工具（长 SQL 等）执行中无法打断，须等工具返回。
停止粒度 = 模型调用边界，延迟受单次工具执行时长约束（通常秒级）。

thread_id 来源：中间件运行在 graph 执行上下文内，用 langgraph.config
的 get_config() 取 RunnableConfig（ModelRequest/Runtime 均不带 config，
已实测确认）。护栏 fail-open：拿不到 config 直接放行，绝不成为生产故障点。

模块身份陷阱（实测踩过）：analyst.py 经垫片以顶层身份 `stop_guard` import
本模块；服务端若以 `agent.stop_guard` import 会注册第二个模块对象、两份
独立的 _state 注册表——端点置的标志中间件永远读不到。server/main.py 必须
同样用顶层 `import stop_guard`（且要在 import agent.analyst 之后）。
"""

import threading

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langgraph.config import get_config


class RunCancelled(Exception):
    """本轮运行被用户请求停止（继承 Exception：graph 线程的 except 能接住）。"""


_state: dict[str, threading.Event] = {}
_lock = threading.Lock()


def start_run(thread_id: str) -> None:
    """新运行开始：清除残留取消标志（每次 /chat 流启动时调用）。

    若上轮被停止且用户立刻重发同 thread 的新请求，旧工作线程仍在跑，
    标志必须清——标志是 thread 级的，不清会误杀新轮。
    """
    with _lock:
        _state[thread_id] = threading.Event()


def cancel(thread_id: str) -> None:
    """请求停止：置位该会话的取消事件（best-effort，无进行中运行也无害）。"""
    with _lock:
        _state.setdefault(thread_id, threading.Event()).set()


def is_set(thread_id: str) -> bool:
    with _lock:
        return thread_id in _state and _state[thread_id].is_set()


def _current_thread_id() -> str | None:
    """从 langgraph 运行上下文取 thread_id；取不到返回 None（fail-open）。"""
    try:
        cfg = get_config()
    except Exception:
        return None
    configurable = (cfg or {}).get("configurable") or {}
    return configurable.get("thread_id")


class CancellationMiddleware(AgentMiddleware):
    """每个模型调用前检查取消标志（同步 + 异步双实现，镜像 model_guard 模式：
    langgraph dev 走异步路径，只实现同步版会在异步上下文抛 NotImplementedError）。

    装配位置：middleware 列表最后 = 最内层（langchain「列表首位 = 最外层」），
    这样 model_guard 的 nudge 重试也会经过取消检查。
    """

    def wrap_model_call(self, request: ModelRequest, handler) -> ModelResponse:
        tid = _current_thread_id()
        if tid and is_set(tid):
            raise RunCancelled(tid)
        return handler(request)

    async def awrap_model_call(self, request: ModelRequest, handler) -> ModelResponse:
        tid = _current_thread_id()
        if tid and is_set(tid):
            raise RunCancelled(tid)
        return await handler(request)
