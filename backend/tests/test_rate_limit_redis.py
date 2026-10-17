"""Redis 共享令牌桶限流测试（S1-C，fakeredis）。

覆盖：
- 多实例共享计数：两个 limiter 实例共享同一 FakeServer（模拟多后端实例共用 Redis），
  总配额不叠加（A 用尽后 B 立即受限）
- IP 隔离：一个 IP 超限不影响其他 IP
- 禁用开关：per_minute=0 不触碰 Redis，恒放行
- 运行期 Redis 异常 → 降级进程内令牌桶（服务不中断）
- 降级警告节流：同类警告 60 秒内只记一次（防 Redis 宕机刷屏）
- 工厂选择：无 URL / 无 redis 包 / 禁用 → 内存；有 URL → Redis

fakeredis 使用注意（同 test_tasks_store.py）：
- FakeAsyncRedisMixin 需先于 redis.asyncio.Redis（MRO 顺序）
- 各实例共享同一 FakeServer，保证跨 loop / 跨实例数据可见
"""
import asyncio
import logging

import pytest
import redis.asyncio as redis_async

fakeredis = pytest.importorskip("fakeredis")
fakeredis_aioredis = pytest.importorskip("fakeredis.aioredis")

from app.services.rate_limit import MemoryRateLimiter, RedisRateLimiter, _build_limiter


class _FakeAsyncRedis(fakeredis_aioredis.FakeAsyncRedisMixin, redis_async.Redis):
    """fakeredis 2.30+：FakeAsyncRedisMixin 先于 redis.asyncio.Redis（MRO 注入 fake 连接类）。"""


def _multi_instance_limiters(per_minute: int, count: int = 2) -> list[RedisRateLimiter]:
    """count 个 limiter 共享同一 FakeServer，模拟多后端实例共用 Redis。"""
    server = fakeredis.FakeServer()
    factory = lambda _url: _FakeAsyncRedis(server=server)  # noqa: E731
    return [RedisRateLimiter(per_minute, "redis://localhost", client_factory=factory) for _ in range(count)]


def test_multi_instance_share_bucket_quota():
    """多实例共享同一 Redis：总配额不叠加，实例 A 用尽后实例 B 立即受限。"""
    instance_a, instance_b = _multi_instance_limiters(per_minute=2)

    async def scenario():
        assert await instance_a.allow("1.2.3.4")
        assert await instance_a.allow("1.2.3.4")
        assert not await instance_a.allow("1.2.3.4")  # A 本实例超限
        assert not await instance_b.allow("1.2.3.4")  # B 同样受限（共享计数，而非各自 2 次）

    asyncio.run(scenario())


def test_multi_instance_ip_isolation():
    (instance,) = _multi_instance_limiters(per_minute=1, count=1)

    async def scenario():
        assert await instance.allow("1.1.1.1")
        assert not await instance.allow("1.1.1.1")
        assert await instance.allow("2.2.2.2")  # 其他 IP 不受影响

    asyncio.run(scenario())


def test_redis_limiter_disabled_when_zero():
    (instance,) = _multi_instance_limiters(per_minute=0, count=1)

    async def scenario():
        assert all([await instance.allow("ip") for _ in range(50)])

    asyncio.run(scenario())


class _BrokenClient:
    """pipeline() 即抛错，模拟 Redis 不可用。"""

    def pipeline(self, *args, **kwargs):
        raise RuntimeError("redis connection refused")


def test_runtime_redis_failure_degrades_to_memory(caplog):
    """运行期 Redis 异常：降级进程内令牌桶（容量 1），服务不中断。"""
    limiter = RedisRateLimiter(per_minute=1, url="redis://localhost", client_factory=lambda _u: _BrokenClient())

    with caplog.at_level(logging.WARNING):
        async def scenario():
            assert await limiter.allow("ip")  # 降级后第一次放行
            assert not await limiter.allow("ip")  # 降级内存桶容量 1，第二次拒绝

        asyncio.run(scenario())

    assert any("降级" in record.getMessage() for record in caplog.records)


def test_degraded_warning_is_throttled(caplog):
    """Redis 宕机时同类警告 60 秒内只记一次，避免日志被刷爆。"""
    limiter = RedisRateLimiter(per_minute=100, url="redis://localhost", client_factory=lambda _u: _BrokenClient())

    with caplog.at_level(logging.WARNING):
        async def scenario():
            for _ in range(30):
                await limiter.allow("ip")

        asyncio.run(scenario())

    warnings = [r for r in caplog.records if "降级" in r.getMessage()]
    assert len(warnings) == 1


def test_build_limiter_selection_and_degradation(monkeypatch):
    from app.services import rate_limit as rate_limit_module

    # 禁用（0）不触碰 Redis
    assert isinstance(_build_limiter(0, "redis://localhost"), MemoryRateLimiter)
    # 未配置 REDIS_URL → 内存
    assert isinstance(_build_limiter(10, ""), MemoryRateLimiter)
    # 配置 REDIS_URL + redis 可用 → Redis
    assert isinstance(_build_limiter(10, "redis://localhost"), RedisRateLimiter)
    # redis 包缺失 → 内存降级
    monkeypatch.setattr(rate_limit_module, "_redis_asyncio", None)
    assert isinstance(_build_limiter(10, "redis://localhost"), MemoryRateLimiter)
