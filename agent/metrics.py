"""进程级计数器：模型轮次护栏（空轮/修复）与轨迹护栏的观测埋点。

定位（铁律 2 延伸）：计数器是纯观测设施，藏在工具/中间件内部——
LLM 不可见不可绕，eval 与生产共用同一份埋点逻辑。

设计取舍：
- 线程安全：eval 并发 attempt（ThreadPoolExecutor + 每 attempt 独立线程）、
  async 服务多会话并发都会同时 bump，read-modify-write 必须加锁
- 只计数不聚合：get_snapshot() 返回原始计数，率/均值由调用方算
  （eval 报告层算空轮率/修复率/无效工具调用率）
- reset() 供评估进程开头清零（仿 eval_scoring 的 judge 计数模式）

计数口径：
- model_calls_total：进入 _EmptyTurnRetryMiddleware 守护的模型调用次数
  （空轮率/无效工具调用率的分母）
- empty_turns：模型返回既无文本又无工具调用的"空轮"次数
- nudge_retries：空轮后 nudge 重试次数；nudge_recovered：重试后恢复可用的次数
- invalid_tool_calls：畸形工具调用条数（长 JSON 被截断等）
- repaired_calls：json-repair 本地修复成功的次数；repair_failed：修复失败次数
"""

import threading

_counter: dict[str, int] = {}
_lock = threading.Lock()

# 合法键白名单：拼错 key 直接报错，不给静默计数 0 的机会
_KEYS = {
    "model_calls_total", "empty_turns", "nudge_retries", "nudge_recovered",
    "invalid_tool_calls", "repaired_calls", "repair_failed",
}


def bump(key: str, n: int = 1) -> None:
    """计数器 +n（key 不在白名单 → ValueError）。"""
    if key not in _KEYS:
        raise ValueError(f"未知计数键: {key}")
    with _lock:
        _counter[key] = _counter.get(key, 0) + n


def get_snapshot() -> dict[str, int]:
    """当前计数快照（未计数的键补 0，报告层无需 .get 兜底）。"""
    with _lock:
        return {k: _counter.get(k, 0) for k in sorted(_KEYS)}


def reset() -> None:
    """全部清零（评估进程开头调用）。"""
    with _lock:
        _counter.clear()
