"""请求限流（任务 H + S1-C）：令牌桶，Redis 共享计数（多实例）+ 内存降级。

- 仅拦截写操作端点（POST）：GET 轮询不限（前端每秒轮询会打爆限额）
- IP 取 X-Forwarded-For 首段（经同源代理时为真实客户端），缺省用 socket 直连地址
- 超限返回 429 { error: { code: RATE_LIMITED, message: 中文提示 } }，与全局错误契约同形
- 令牌桶：容量 = per_minute，按 per_minute/60 每秒回填（跨线程安全）

存储（S1-C）：
- RedisRateLimiter：复用 S1-A 的 REDIS_URL，多后端实例共享同一 Redis 计数；
  用 WATCH/MULTI/EXEC 乐观事务保证「读改写」原子性（不依赖 Lua/EVAL，兼容 fakeredis）
- MemoryRateLimiter：进程内令牌桶（无 REDIS_URL / redis 包缺失 / 运行期 Redis 故障时）
- 运行期 Redis 异常自动降级内存并打警告，服务不中断；同类警告默认 60 秒只记一次，防刷屏
"""
import asyncio
import json
import logging
import threading
import time
from typing import Protocol

from app.config import RATE_LIMIT_PER_MINUTE, REDIS_URL

logger = logging.getLogger(__name__)

_RATE_LIMITED_BODY = json.dumps(
    {"error": {"code": "RATE_LIMITED", "message": "请求太频繁，请稍后再试"}}
).encode("utf-8")

# 可选依赖：redis 包仅 Redis 限流模式需要（requirements-redis.txt）；
# 缺失时 _build_limiter 降级内存，模块导入不炸
try:
    import redis.asyncio as _redis_asyncio
    from redis.exceptions import WatchError as _WatchError
except ImportError:  # pragma: no cover - 未安装 redis 时
    _redis_asyncio = None  # type: ignore[assignment]
    _WatchError = None  # type: ignore[assignment]


class TokenBucketLimiter:
    """每 key 一个令牌桶：容量 capacity，恒速回填 rate = per_minute/60 每秒（进程内）。"""

    def __init__(self, per_minute: int):
        self.per_minute = int(per_minute)
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        if self.per_minute <= 0:
            return True  # 0 = 禁用限流
        now = time.monotonic() if now is None else now
        refill_per_second = self.per_minute / 60.0
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.per_minute), now))
            tokens = min(float(self.per_minute), tokens + (now - last) * refill_per_second)
            allowed = tokens >= 1.0
            if allowed:
                tokens -= 1.0
            self._buckets[key] = (tokens, now)
            return allowed


class RateLimiter(Protocol):
    """限流器统一接口（async，便于 Redis 实现）。"""

    per_minute: int

    async def allow(self, key: str) -> bool: ...


class MemoryRateLimiter:
    """进程内令牌桶（默认实现；单实例部署或 Redis 降级时使用）。"""

    def __init__(self, per_minute: int):
        self.per_minute = int(per_minute)
        self._bucket = TokenBucketLimiter(per_minute)

    async def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        return self._bucket.allow(key)


class RedisRateLimiter:
    """基于 Redis 的共享令牌桶（多实例部署：总配额全局一致）。

    - 状态存 Redis Hash（字段 tokens / ts），WATCH + MULTI/EXEC 乐观重试保证读改写原子；
      并发冲突（WatchError）自动重试，重试耗尽则保守降级内存。
    - 连接池绑定 event loop 不可跨 loop 复用 → 每个 loop 独立 client
      （与 RedisTaskStore 同款约定；client_factory 供测试注入 fakeredis）。
    - 运行期异常（连接失败 / 超时等）降级为进程内令牌桶，并打警告（默认 60 秒一次，防刷屏）。
    """

    _KEY_PREFIX = "ratelimit:"
    _BUCKET_TTL_MS = 120_000  # 覆盖整桶回填周期（60s）的 2 倍
    _MAX_RETRIES = 5
    _WARN_INTERVAL_SECONDS = 60.0

    def __init__(self, per_minute: int, url: str, client_factory=None) -> None:
        self.per_minute = int(per_minute)
        self._url = url
        self._client_factory = client_factory
        self._clients: dict[asyncio.AbstractEventLoop, "redis.asyncio.Redis"] = {}
        self._fallback = TokenBucketLimiter(per_minute)
        self._warn_lock = threading.Lock()
        self._last_warn = float("-inf")

    def _client(self) -> "redis.asyncio.Redis":
        if _redis_asyncio is None:
            raise RuntimeError("redis 包未安装：pip install -r requirements-redis.txt")
        loop = asyncio.get_running_loop()
        client = self._clients.get(loop)
        if client is None:
            if self._client_factory is not None:
                client = self._client_factory(self._url)
            else:
                client = _redis_asyncio.from_url(self._url, decode_responses=True)
            self._clients[loop] = client
        return client

    def _warn_degraded(self, exc: Exception) -> None:
        """降级警告节流：同间隔内只记一次，避免 Redis 宕机时日志被刷爆。"""
        now = time.monotonic()
        with self._warn_lock:
            if now - self._last_warn < self._WARN_INTERVAL_SECONDS:
                return
            self._last_warn = now
        logger.warning(
            "Redis 限流不可用，降级为进程内令牌桶（%d 秒内同类警告只记一次）：%s",
            int(self._WARN_INTERVAL_SECONDS),
            exc,
        )

    @staticmethod
    def _as_float(value) -> float | None:
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8", "ignore")
        return float(value)

    async def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        try:
            return await self._allow_redis(key)
        except Exception as exc:  # 网络/连接/协议等运行期异常：降级内存，服务不中断
            self._warn_degraded(exc)
            return self._fallback.allow(key)

    async def _allow_redis(self, key: str) -> bool:
        client = self._client()
        redis_key = f"{self._KEY_PREFIX}{key}"
        capacity = float(self.per_minute)
        refill_per_second = capacity / 60.0
        for _ in range(self._MAX_RETRIES):
            async with client.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(redis_key)
                    tokens_raw, ts_raw = await pipe.hmget(redis_key, "tokens", "ts")
                    now = time.time()
                    tokens = self._as_float(tokens_raw)
                    last = self._as_float(ts_raw)
                    tokens = capacity if tokens is None else tokens
                    last = now if last is None else last
                    tokens = min(capacity, tokens + max(0.0, now - last) * refill_per_second)
                    allowed = tokens >= 1.0
                    if allowed:
                        tokens -= 1.0
                    pipe.multi()
                    pipe.hset(redis_key, mapping={"tokens": tokens, "ts": now})
                    pipe.pexpire(redis_key, self._BUCKET_TTL_MS)
                    await pipe.execute()
                    return allowed
                except Exception as exc:  # 乐观并发冲突 → 重试；其它异常上抛（由 allow 降级）
                    if _WatchError is not None and isinstance(exc, _WatchError):
                        continue
                    raise
        # 连续冲突重试耗尽（高并发极端情形）：保守降级内存
        return self._fallback.allow(key)


def _build_limiter(per_minute: int, redis_url: str | None) -> RateLimiter:
    """按配置构造限流器：Redis 共享（多实例）；缺 URL/包或禁用时降级内存。"""
    if per_minute <= 0:
        return MemoryRateLimiter(per_minute)  # 0 = 禁用：不触碰 Redis
    if not redis_url:
        return MemoryRateLimiter(per_minute)
    if _redis_asyncio is None:
        logger.warning(
            "REDIS_URL 已配置但 redis 包未安装（pip install -r requirements-redis.txt），限流降级为进程内令牌桶"
        )
        return MemoryRateLimiter(per_minute)
    return RedisRateLimiter(per_minute, redis_url)


_LIMITER: RateLimiter = _build_limiter(RATE_LIMIT_PER_MINUTE, REDIS_URL)


def _client_ip(scope: dict) -> str:
    # 同源代理场景取 X-Forwarded-For 首段；否则用直连地址
    for name, value in scope.get("headers") or []:
        if name == b"x-forwarded-for":
            first = (value.decode("latin-1").split(",")[0] or "").strip()
            if first:
                return first
    client = scope.get("client")
    return client[0] if client else "unknown"


class RateLimitMiddleware:
    """纯 ASR 中间件：仅对 POST 做令牌桶限流。"""

    def __init__(self, app, limiter: RateLimiter | None = None):
        self.app = app
        self.limiter = limiter or _LIMITER

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") == "POST" and self.limiter.per_minute > 0:
            if not await self.limiter.allow(_client_ip(scope)):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 429,
                        "headers": [(b"content-type", b"application/json; charset=utf-8")],
                    }
                )
                await send({"type": "http.response.body", "body": _RATE_LIMITED_BODY})
                return
        await self.app(scope, receive, send)
