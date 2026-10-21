"""同步桥接（S2-P0）：常驻事件循环复用，避免每次 store 调用泄漏连接。

旧实现 `asyncio.run(coro)` 每次新建 event loop，而 RedisTaskStore 以 loop 为 key
缓存 client 且只增不减 → 每次调用泄漏一个连接池。现在所有桥接调用共用一个 loop。
"""
import asyncio

import pytest
import redis.asyncio as redis_async

fakeredis = pytest.importorskip("fakeredis")
fakeredis_aioredis = pytest.importorskip("fakeredis.aioredis")

from app.schemas import VideoInfo
from app.services import tasks as tasks_module
from app.services.tasks import RedisTaskStore, create_task, get_task, update_task

VIDEO = VideoInfo(title="测试视频", cover="https://i1.hdslb.com/c.jpg", duration=135, platform="bilibili", videoId="BV1GJ411x7h7")


class _FakeAsyncRedis(fakeredis_aioredis.FakeAsyncRedisMixin, redis_async.Redis):
    pass


@pytest.fixture()
def redis_store(monkeypatch):
    server = fakeredis.FakeServer()
    store = RedisTaskStore(
        "redis://localhost", client_factory=lambda _url: _FakeAsyncRedis(server=server)
    )
    monkeypatch.setattr(tasks_module, "_STORE", store)
    return store


def test_bridge_reuses_single_client(redis_store):
    """多次 create/get/update 后仍只有一个 client（旧实现是「调用次数」个）。"""
    task_id = create_task(VIDEO)
    for _ in range(10):
        get_task(task_id)
        update_task(task_id, progress=1)
    assert len(redis_store._clients) == 1


def test_bridge_roundtrip(redis_store):
    task_id = create_task(VIDEO, engine="cloud")
    update_task(task_id, status="completed", progress=100, attempts=1)
    task = get_task(task_id)
    assert task is not None
    assert task.status == "completed" and task.progress == 100
    assert task.engine == "cloud" and task.attempts == 1


def test_bridge_raises_inside_event_loop(redis_store):
    """在已有事件循环里调用桥接必须报错（否则会死锁），提示改用 await。"""

    async def _call():
        with pytest.raises(RuntimeError, match="不能在事件循环内调用"):
            get_task("any-task")

    asyncio.run(_call())
