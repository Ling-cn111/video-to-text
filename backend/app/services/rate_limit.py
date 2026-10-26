"""请求限流（任务 H + S1-C + S3 安全修复）：令牌桶，Redis 共享计数（多实例）+ 内存降级。

- 仅拦截写操作端点（POST）：GET 轮询不限（前端每秒轮询会打爆限额）
- 客户端标识（S3 安全修复）：默认只认 TCP 对端地址，**完全忽略 X-Forwarded-For**；
  只有 TRUSTED_PROXY_CIDRS 配了、且对端命中时才解析该头，并从右往左跳过可信代理
  取第一个不可信地址（标准算法）。IPv6 按 /64 归并（单台主机通常掌握整个 /64，
  逐地址计桶等于不限流）。
- 两级配额（S3 安全修复）：每客户端 RATE_LIMIT_PER_MINUTE + 全站
  RATE_LIMIT_GLOBAL_PER_MINUTE。前者在标识可伪造/可塌缩时形同虚设，后者才是
  钱包兜底——API 无鉴权，烧的是部署者的 ASR / LLM Key。
- 超限返回 429 { error: { code: RATE_LIMITED, message: 中文提示 } } + Retry-After，
  与全局错误契约同形
- 令牌桶：容量 = per_minute，按 per_minute/60 每秒回填（跨线程安全）

存储（S1-C）：
- RedisRateLimiter：复用 S1-A 的 REDIS_URL，多后端实例共享同一 Redis 计数；
  用 WATCH/MULTI/EXEC 乐观事务保证「读改写」原子性（不依赖 Lua/EVAL，兼容 fakeredis）
- MemoryRateLimiter：进程内令牌桶（无 REDIS_URL / redis 包缺失 / 运行期 Redis 故障时）
- 运行期 Redis 异常自动降级内存并打警告，服务不中断；同类警告默认 60 秒只记一次，防刷屏
"""
import asyncio
import ipaddress
import json
import logging
import math
import threading
import time
from typing import Protocol

from app.config import RATE_LIMIT_GLOBAL_PER_MINUTE, RATE_LIMIT_PER_MINUTE, REDIS_URL, TRUSTED_PROXY_CIDRS

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
    """进程内令牌桶（默认实现；单实例部署或 Redis 降级时使用）。

    `per_minute` 是转发到内部桶的 property，不是独立字段：两处各存一份会出现
    「改了外层配额、内层桶仍按旧值放行」的分叉（测试里 monkeypatch 就踩到过）。
    """

    def __init__(self, per_minute: int):
        self._bucket = TokenBucketLimiter(per_minute)

    @property
    def per_minute(self) -> int:
        return self._bucket.per_minute

    @per_minute.setter
    def per_minute(self, value: int) -> None:
        self._bucket.per_minute = int(value)

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
_GLOBAL_LIMITER: RateLimiter = _build_limiter(RATE_LIMIT_GLOBAL_PER_MINUTE, REDIS_URL)

# 全站配额共用一个桶：key 固定，所有客户端累加到同一个计数器
_GLOBAL_KEY = "__global__"

# 兜底上限：未配置或解析失败时按「不信任任何转发头」处理（安全默认）
_UNKNOWN_CLIENT = "unknown"


def _parse_trusted_cidrs(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """解析 TRUSTED_PROXY_CIDRS：逗号分隔的 CIDR（也接受单个 IP）。

    解析失败的条目跳过并打警告，而不是让整个应用启动失败——限流配置写错
    不该导致服务不可用，退化为「不信任」即安全默认。
    """
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for chunk in (raw or "").split(","):
        item = chunk.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.warning("TRUSTED_PROXY_CIDRS 中的「%s」不是合法 CIDR，已忽略", item)
    return tuple(networks)


_TRUSTED_NETWORKS = _parse_trusted_cidrs(TRUSTED_PROXY_CIDRS)


def _parse_ip(raw: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """宽松解析 IP：容忍端口后缀与方括号（uvicorn 的 client 元组已拆好端口，
    但转发头里常带 "1.2.3.4:5678" 或 "[::1]:5678"）。"""
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    if text.startswith("["):  # [::1]:1234
        text = text[1:].split("]", 1)[0]
    elif text.count(":") == 1:  # 1.2.3.4:1234（IPv6 本身多冒号，不会走到这里）
        text = text.split(":", 1)[0]
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return None
    # IPv4-mapped IPv6（::ffff:1.2.3.4）归一成 IPv4，避免同一客户端两个桶
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _bucket_key(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """地址 → 限流桶 key。

    IPv6 按 /64 归并：运营商给单个用户分配 /64 是常态，逐地址计桶等于不限流
    （攻击者在自己 /64 内换地址即可无限刷）。
    """
    if isinstance(addr, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def _is_trusted(addr, networks) -> bool:
    return any(addr in network for network in networks)


def _forwarded_chain(scope: dict) -> list[str]:
    """收集 X-Forwarded-For 的所有取值（可能有多条同名头，按出现顺序拼接）。"""
    parts: list[str] = []
    for name, value in scope.get("headers") or []:
        if name == b"x-forwarded-for":
            parts.extend(value.decode("latin-1").split(","))
    return [p.strip() for p in parts if p.strip()]


def _client_ip(scope: dict) -> str:
    """解析限流用的客户端标识（S3 安全修复，安全默认是「不信任转发头」）。

    规则：
    1. 取 TCP 对端地址。若未配置 TRUSTED_PROXY_CIDRS，或对端不在可信网段内，
       直接返回对端地址，**完全不看 X-Forwarded-For**——伪造该头无效。
    2. 只有对端可信时才解析转发链，并从右往左跳过可信代理，取第一个不可信地址。
       X-Forwarded-For 的语义是「客户端, 代理1, 代理2…」，越靠右越接近服务端、
       越可信；攻击者能控制的只有最左边的部分，所以从右往左找才是标准做法。
    3. 整条链都可信（极端情况）时取最左端，退化为「记录最初发起方」。
    """
    client = scope.get("client")
    peer = _parse_ip(client[0]) if client else None
    if peer is None:
        return _UNKNOWN_CLIENT
    if not _TRUSTED_NETWORKS or not _is_trusted(peer, _TRUSTED_NETWORKS):
        return _bucket_key(peer)

    hops = [addr for addr in (_parse_ip(raw) for raw in _forwarded_chain(scope)) if addr is not None]
    if not hops:
        return _bucket_key(peer)
    for addr in reversed(hops + [peer]):
        if not _is_trusted(addr, _TRUSTED_NETWORKS):
            return _bucket_key(addr)
    return _bucket_key(hops[0])


def _retry_after_seconds(per_minute: int) -> int:
    """429 的 Retry-After：桶空时补满 1 个令牌所需秒数（向上取整，至少 1）。"""
    if per_minute <= 0:
        return 1
    return max(1, math.ceil(60 / per_minute))


def _is_infra_address(addr) -> bool:
    """判断对端地址是否「基础设施地址」而非真实终端用户。

    命中任一即认为是网关/代理/容器内网，不是可用来区分用户的标识：
    - 私有网段（10/8、172.16/12、192.168/16）
    - 回环（127/8、::1）
    - 链路本地（169.254/16、fe80::/10）
    - CGNAT（100.64/10，阿里云 FC / 容器网络常用）
    - 未指定地址（0.0.0.0、::）

    为什么要区分：Serverless 网关若不注入真实客户端 IP，`scope["client"]`
    对所有请求都是同一个内网地址。此时拿它当「每客户端」桶 key，等于把
    **全站用户塞进同一个 10 次/分钟的桶**——一个用户刷满，所有人都被 429
    （S3 实测复现：中国直连耗尽配额后，德国代理立刻收到 429）。
    """
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified:
        return True
    if isinstance(addr, ipaddress.IPv4Address):
        # 100.64.0.0/10（RFC 6598 CGNAT）：ipaddress 不把这段算作 private
        return addr in _CGNAT_NETWORK
    return False


_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def _should_limit_per_client(scope: dict) -> tuple[str, bool]:
    """返回 (桶 key, 是否应启用每客户端限流)。

    当客户端标识是基础设施地址时，它无法区分用户，按它计桶只会误伤所有人；
    此时**关闭每客户端限流**，交由全站配额兜底（那层本来就是为「标识不可信」
    的场景准备的）。
    """
    key = _client_ip(scope)
    addr = _parse_ip(key.split("/")[0]) if key != _UNKNOWN_CLIENT else None
    if addr is None:
        return key, False
    if _is_infra_address(addr):
        return key, False
    return key, True


class RateLimitMiddleware:
    """纯 ASR 中间件：仅对 POST 做令牌桶限流（每客户端 + 全站两级）。"""

    def __init__(
        self,
        app,
        limiter: RateLimiter | None = None,
        global_limiter: RateLimiter | None = None,
    ):
        self.app = app
        self.limiter = limiter or _LIMITER
        self.global_limiter = global_limiter or _GLOBAL_LIMITER

    @staticmethod
    async def _reject(send, per_minute: int) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"retry-after", str(_retry_after_seconds(per_minute)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _RATE_LIMITED_BODY})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        # 先查全站总量：客户端标识可能被伪造或全体塌缩成同一个值，
        # 这一层是「标识不可信时仍然生效」的最后防线。
        if self.global_limiter.per_minute > 0 and not await self.global_limiter.allow(_GLOBAL_KEY):
            logger.warning(
                "全站限流触发（每分钟 %d 次 POST 上限）：客户端 %s",
                self.global_limiter.per_minute,
                _client_ip(scope),
            )
            await self._reject(send, self.global_limiter.per_minute)
            return

        if self.limiter.per_minute > 0:
            key, per_client = _should_limit_per_client(scope)
            # 标识不可信（内网/网关地址）时跳过每客户端配额：拿它计桶等于
            # 把全站用户塞进同一个桶，一个用户刷满所有人都被 429。
            if per_client and not await self.limiter.allow(key):
                await self._reject(send, self.limiter.per_minute)
                return

        await self.app(scope, receive, send)
