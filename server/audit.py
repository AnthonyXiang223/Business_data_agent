"""审计日志：ASGI 中间件 + 独立小连接池落 PG（audit_logs 表）。

设计取舍：
- 审计用独立连接池，不与 checkpointer 池竞争；写入走线程池，不阻塞事件循环
- 每请求一行：中间件采集请求级字段（method/path/状态码/耗时/来源 IP），
  /chat 端点通过 request.state.audit 补会话级字段（thread_id/输入输出长度/
  工具调用数/token），SSE 流最后一个 body 发出时统一落库——天然等对话完整结束才记账
- 审计写入失败只告警不抛错：审计不能影响业务响应
- 已知边界：客户端中途断开（SSE 流无收尾 body）时该请求无审计行，v1 接受
"""

import logging
import time

import anyio
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

logger = logging.getLogger("server.audit")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGSERIAL PRIMARY KEY,   
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),  
    method TEXT NOT NULL,   
    path TEXT NOT NULL,
    status_code INT,
    duration_ms DOUBLE PRECISION,
    client_host TEXT,
    thread_id TEXT,
    user_id TEXT,
    input_chars INT,
    output_chars INT,
    tool_calls INT,
    tokens INT,
    run_state TEXT
)
"""

# 维护需要插入的全部业务字段名
_FIELDS = [
    "method", "path", "status_code", "duration_ms", "client_host", "thread_id",
    "user_id", "input_chars", "output_chars", "tool_calls", "tokens", "run_state",
]

_INSERT = (
    "INSERT INTO audit_logs ("
    + ", ".join(_FIELDS)
    + ") VALUES ("
    + ", ".join(["%s"] * len(_FIELDS))
    + ")"
)

_pool: ConnectionPool | None = None


def init(url: str) -> None:
    """服务启动时建表 + 打开审计专用连接池。"""
    global _pool
    _pool = ConnectionPool(
        conninfo=url,
        min_size=1,
        max_size=4,
        open=True,
        kwargs={"autocommit": True, "row_factory": dict_row},
    )
    with _pool.connection() as conn:
        conn.execute(_CREATE_TABLE)


def close() -> None:
    if _pool is not None:
        _pool.close()
        _pool = None


def _write_sync(fields: dict) -> None:
    with _pool.connection() as conn:
        conn.execute(_INSERT, [fields.get(k) for k in _FIELDS])


async def write_row(**fields) -> None:
    """异步落一行审计；调用方可 await 保证已落库。"""
    if _pool is None:
        return
    try:
        await anyio.to_thread.run_sync(_write_sync, fields)
    except Exception:
        logger.warning("审计写入失败: %s", fields, exc_info=True)


async def list_rows(limit: int = 50, thread_id: str | None = None) -> list[dict]:
    """按时间倒序查审计行（管理接口用）。"""
    if _pool is None:
        return []

    def _query() -> list[dict]:
        with _pool.connection() as conn:
            if thread_id:
                rows = conn.execute(
                    "SELECT * FROM audit_logs WHERE thread_id = %s "
                    "ORDER BY id DESC LIMIT %s",
                    (thread_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit_logs ORDER BY id DESC LIMIT %s", (limit,)
                ).fetchall()
            return rows

    return await anyio.to_thread.run_sync(_query)


class AuditMiddleware:
    """每 HTTP 请求记一行审计。

    端点通过 request.state.audit（dict）补字段；状态码在响应开始、耗时在
    最后一个 body 发出前补上，最后统一落库。写入是 await 的——审计行
    一定在客户端收到完整响应前落库，查 /audit 不会看到"半截账"。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        fields = {
            "method": scope["method"],
            "path": scope["path"],
            "status_code": None,
            "duration_ms": None,
            "client_host": client[0] if client else None,
            "thread_id": None,
            "user_id": None,
            "input_chars": None,
            "output_chars": None,
            "tool_calls": None,
            "tokens": None,
            "run_state": None,
        }
        scope.setdefault("state", {})["audit"] = fields
        started = time.monotonic()

        async def wrapped_send(message):
            if message["type"] == "http.response.start":
                fields["status_code"] = message["status"]
            if message["type"] == "http.response.body" and not message.get("more_body"):
                fields["duration_ms"] = (time.monotonic() - started) * 1000
                await write_row(**fields)
            await send(message)

        await self.app(scope, receive, wrapped_send)
