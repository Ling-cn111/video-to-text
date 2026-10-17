"""TaskStore 抽象（S1-A）：memory 与 redis（fakeredis）行为一致性。

同一组用例参数化跑在两个实现上，确保 TASK_STORE 切换不改行为；
另覆盖 REDIS_URL 缺失时 redis 模式降级 memory 的告警路径。

fakeredis 使用注意（fakeredis >= 2.30）：
- FakeAsyncRedis 已改为 FakeAsyncRedisMixin，需与 redis.asyncio.Redis 组合，
  且 MRO 顺序必须是 Mixin 在前：class FakeAsyncRedis(FakeAsyncRedisMixin, Redis)
  （Mixin 的 __init__ 先注入 fake 连接类，顺序反了会走真实网络）
- asyncio.run 每次新建 event loop，RedisTaskStore 为每个 loop 缓存独立 client；
  所有 client 必须共享同一 FakeServer，否则跨 loop 数据不可见。
"""
import asyncio
import logging

import pytest
import redis.asyncio as redis_async

fakeredis = pytest.importorskip("fakeredis")
fakeredis_aioredis = pytest.importorskip("fakeredis.aioredis")

from app.schemas import TranscriptItem, VideoInfo
from app.services.tasks import MemoryTaskStore, RedisTaskStore, _build_store

VIDEO = VideoInfo(title="测试视频", cover="https://i1.hdslb.com/c.jpg", duration=135, platform="bilibili", videoId="BV1GJ411x7h7")


class _FakeAsyncRedis(fakeredis_aioredis.FakeAsyncRedisMixin, redis_async.Redis):
    """fakeredis 2.30+：FakeAsyncRedisMixin 需先于 redis.asyncio.Redis（MRO 顺序）注入 fake 连接类。"""


def _make_redis_store() -> RedisTaskStore:
    """fakeredis 客户端注入（client_factory 绕开真实网络）。

    同一 FakeServer 被所有 client 共享：asyncio.run 每次新建 event loop，
    store 会为每个 loop 缓存独立 client（连接池绑定 loop 不可跨 loop 复用），
    共享 server 保证跨 loop 数据可见——与生产模式共享真实 Redis 服务器语义一致。
    """
    server = fakeredis.FakeServer()
    return RedisTaskStore("redis://localhost", client_factory=lambda _url: _FakeAsyncRedis(server=server))


@pytest.fixture(params=["memory", "redis"])
def store(request, tmp_path):
    """同一组行为用例在 memory / redis 两种实现上各跑一遍。"""
    if request.param == "redis":
        return _make_redis_store()
    return MemoryTaskStore(str(tmp_path / "tasks.db"))


def test_create_and_get_roundtrip(store):
    task_id = asyncio.run(store.create(VIDEO, engine="cloud"))
    task = asyncio.run(store.get(task_id))
    assert task is not None
    assert task.status == "processing"
    assert task.stage == "parse_link"
    assert task.progress == 0
    assert task.engine == "cloud"
    assert task.video is not None and task.video.title == "测试视频"
    assert task.transcript == []


def test_get_unknown_task_returns_none(store):
    assert asyncio.run(store.get("no-such-task")) is None


def test_update_persists_fields(store):
    task_id = asyncio.run(store.create(VIDEO))
    asyncio.run(store.update(task_id, status="completed", progress=100, transcript_source="subtitle_ai", plain_text="全文"))
    task = asyncio.run(store.get(task_id))
    assert task is not None
    assert task.status == "completed" and task.progress == 100
    assert task.transcript_source == "subtitle_ai" and task.plain_text == "全文"


def test_update_transcript_roundtrip(store):
    task_id = asyncio.run(store.create(VIDEO))
    items = [TranscriptItem(time=1.5, text="你好"), TranscriptItem(time=3.0, text="世界")]
    asyncio.run(store.update(task_id, transcript=items))
    task = asyncio.run(store.get(task_id))
    assert [(i.time, i.text) for i in task.transcript] == [(1.5, "你好"), (3.0, "世界")]


def test_update_unknown_task_is_noop(store):
    asyncio.run(store.update("no-such-task", status="completed"))  # 不抛


def test_updated_at_advances_on_update(store):
    task_id = asyncio.run(store.create(VIDEO))
    before = asyncio.run(store.get(task_id)).updated_at
    asyncio.run(store.update(task_id, progress=10))
    after = asyncio.run(store.get(task_id)).updated_at
    assert after >= before


def test_init_marks_processing_tasks_interrupted(store):
    """启动加载：status=processing 的任务被标记 interrupted，已完成不受影响。"""
    processing_id = asyncio.run(store.create(VIDEO))
    done_id = asyncio.run(store.create(VIDEO))
    asyncio.run(store.update(done_id, status="completed"))

    asyncio.run(store.init())  # 模拟进程重启

    assert asyncio.run(store.get(processing_id)).status == "interrupted"
    assert asyncio.run(store.get(done_id)).status == "completed"


def test_init_is_idempotent(store):
    task_id = asyncio.run(store.create(VIDEO))
    asyncio.run(store.init())
    asyncio.run(store.init())  # 二次 init 不重复处理
    assert asyncio.run(store.get(task_id)).status == "interrupted"


# ---- 工厂与降级 ----

def test_build_store_memory_default():
    assert isinstance(_build_store("memory", None), MemoryTaskStore)


def test_build_store_redis_with_url_builds_redis_store():
    assert isinstance(_build_store("redis", "redis://localhost"), RedisTaskStore)


def test_build_store_redis_without_url_degrades_to_memory(caplog):
    with caplog.at_level(logging.WARNING):
        built = _build_store("redis", None)
    assert isinstance(built, MemoryTaskStore)
    assert "REDIS_URL" in caplog.text


def test_build_store_unknown_mode_falls_back_to_memory(caplog):
    with caplog.at_level(logging.WARNING):
        built = _build_store("nope", None)
    assert isinstance(built, MemoryTaskStore)
