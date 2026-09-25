"""Redis 缓存共享模块：SQL 归一化结果缓存 + 向量检索结果缓存的公共入口。

定位（铁律 2 延伸）：缓存是纯工具层基础设施，藏在工具入口内部——
服务层（server/）不感知、LLM 不可见不可绕。

设计取舍：
- 懒连接 + 单例：第一次 get/set 才连 Redis，agent 启动不付连接成本；
  首次连接失败后同进程不再重试（避免每次工具调用都付 2s 超时）
- 降级优先：Redis 未配置/不可达 → 静默直查。缓存是优化不是依赖，
  绝不让缓存故障改变工具行为（评估/服务/CLI 三种上下文都成立）
- key 统一 `前缀:版本:{sha256(关键字段)}`：版本号写进 key，
  数据/知识库重建后旧缓存自然失效，不依赖精确的过期时间
- 值统一 JSON 字符串；TTL 兜底过期（数据文件只读，TTL 内结果恒定）
"""

import hashlib
import logging
import os
import threading

logger = logging.getLogger(__name__)

# TTL 可通过环境变量调（秒）。SQL 结果默认 1 小时：CSV 静态只读，
# 换数据文件后重启进程即失效（进程内 _df_cache 也是同一语义）；
# 检索结果默认 24 小时：kb 版本已进 key，TTL 只兜底；
# embedding 默认 7 天：模型文件固定，key 带模型名，TTL 只兜底
SQL_TTL = int(os.environ.get("SQL_CACHE_TTL", "3600"))
RAG_TTL = int(os.environ.get("RAG_CACHE_TTL", "86400"))
EMB_TTL = int(os.environ.get("EMB_CACHE_TTL", "604800"))

_client = None
_connection_attempted = False


def _get_client():
    global _client, _connection_attempted
    if _client is not None or _connection_attempted:
        return _client
    _connection_attempted = True
    try:
        import redis  # 懒导入：没装 redis-py 时其余工具完全不受影响

        url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        # protocol=2：RESP2 明文命令，不握手 HELLO——本机 redis-server 5.0.7
        # （Ubuntu 20.04 源）不支持 RESP3 握手（Redis 6.0+ 才有 HELLO），
        # 降级到 RESP2 兼容所有版本，功能无损
        client = redis.Redis.from_url(
            url,
            protocol=2,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        client.ping()
        _client = client
    except Exception as e:
        logger.warning("Redis 不可用，缓存降级为直查（首次失败后不再重试）: %s", e)
    return _client


def digest(*parts: str) -> str:
    """关键字段 → sha256 短摘要（key 的一部分）。"""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


# ---------- 命中计数（效率评估用，纯观测设施） ----------
# 线程安全：eval 并发 attempt、async 服务多会话并发都会经过这里。
# 按 key 前缀（sql/rag）分桶：SQL 缓存与 RAG 缓存各自独立报命中率。

_stats_lock = threading.Lock()
_stats: dict[str, dict[str, int]] = {}


def _bump_stats(prefix: str, key: str) -> None:
    with _stats_lock:
        bucket = _stats.setdefault(prefix, {})
        bucket[key] = bucket.get(key, 0) + 1


def get_stats() -> dict:
    """命中计数快照：{前缀: {hits, misses, bypass, writes}}。"""
    with _stats_lock:
        return {p: dict(b) for p, b in _stats.items()}


def reset_stats() -> None:
    """清零（评估进程开头调用）。"""
    with _stats_lock:
        _stats.clear()


def get(key: str) -> str | None:
    """读缓存；未命中/不可用一律返回 None（调用方当 miss 处理）。

    计数口径：bypass = Redis 不可用的降级直查（与真 miss 语义不同——
    降级不付 2s 超时成本且同进程不再重试，命中率报告里单独列出）。
    """
    prefix = key.split(":", 1)[0]
    client = _get_client()
    if client is None:
        _bump_stats(prefix, "bypass")
        return None
    try:
        value = client.get(key)
    except Exception as e:
        logger.warning("Redis 读取失败: %s", e)
        return None
    _bump_stats(prefix, "hits" if value is not None else "misses")
    return value


def set(key: str, value: str, ttl: int) -> None:
    """写缓存；失败只告警不抛错（本次结果照样返回给调用方）。"""
    client = _get_client()
    if client is None:
        return
    try:
        client.set(key, value, ex=ttl)
        _bump_stats(key.split(":", 1)[0], "writes")
    except Exception as e:
        logger.warning("Redis 写入失败: %s", e)
