"""请求限流（任务 H）：内存令牌桶，每 IP 每分钟 RATE_LIMIT_PER_MINUTE 次 POST（0 = 禁用）。

- 仅拦截写操作端点（POST）：GET 轮询不限（前端每秒轮询会打爆限额）
- IP 取 X-Forwarded-For 首段（经同源代理时为真实客户端），缺省用 socket 直连地址
- 超限返回 429 { error: { code: RATE_LIMITED, message: 中文提示 } }，与全局错误契约同形
- 令牌桶：容量 = per_minute，按 per_minute/60 每秒回填；跨线程安全
"""
import json
import threading
import time

from app.config import RATE_LIMIT_PER_MINUTE

_RATE_LIMITED_BODY = json.dumps(
    {"error": {"code": "RATE_LIMITED", "message": "请求太频繁，请稍后再试"}}
).encode("utf-8")


class TokenBucketLimiter:
    """每 key 一个令牌桶：容量 capacity，恒速回填 rate = per_minute/60 每秒。"""

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


_LIMITER = TokenBucketLimiter(RATE_LIMIT_PER_MINUTE)


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

    def __init__(self, app, limiter: TokenBucketLimiter | None = None):
        self.app = app
        self.limiter = limiter or _LIMITER

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") == "POST" and self.limiter.per_minute > 0:
            if not self.limiter.allow(_client_ip(scope)):
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
