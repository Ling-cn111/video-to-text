"""TaskStore 抽象（S1-A）：memory 与 redis（fakeredis）行为一致性。

同一组用例参数化跑在两个实现上，确保 TASK_STORE 切换不改行为；
另覆盖 REDIS_URL 缺失时 redis 模式降级 memory 的告警路径。

S2-P0 补充：TTL、attempts 往返、init 的「不误杀」语义、同步桥接不泄漏连接。

fakeredis 使用注意（fakeredis >= 2.30）：
- FakeAsyncRedis 已改为 FakeAsyncRedisMixin，需与 redis.asyncio.Redis 组合，
  且 MRO 顺序必须是 Mixin 在前：class FakeAsyncRedis(FakeAsyncRedisMixin, Redis)
  （Mixin 的 __init__ 先注入 fake 连接类，顺序反了会走真实网络）
- 每个 event loop 需要独立 client，所有 client 必须共享同一 FakeServer，
  否则跨 loop 数据不可见。
"""
import asyncio
import logging
import time

import pytest
import redis.asyncio as redis_async

fakeredis = pytest.importorskip("fakeredis")
fakeredis_aioredis = pytest.importorskip("fakeredis.aioredis")

from app.schemas import TranscriptItem, VideoInfo
from app.services import tasks as tasks_module
from app.services.tasks import MemoryTaskStore, RedisTaskStore, _build_store

VIDEO = VideoInfo(title="测试视频", cover="https://i1.hdslb.com/c.jpg", duration=135, platform="bilibili", videoId="BV1GJ411x7h7")


class _FakeAsyncRedis(fakeredis_aioredis.FakeAsyncRedisMixin, redis_async.Redis):
    """fakeredis 2.30+：FakeAsyncRedisMixin 需先于 redis.asyncio.Redis（MRO 顺序）注入 fake 连接类。"""


def _make_redis_store(**kwargs) -> RedisTaskStore:
    """fakeredis 客户端注入（client_factory 绕开真实网络）。

    同一 FakeServer 被所有 client 共享：store 会为每个 event loop 缓存独立 client
    （连接池绑定 loop 不可跨 loop 复用），共享 server 保证跨 loop 数据可见——
    与生产模式共享真实 Redis 服务器语义一致。
    """
    server = fakeredis.FakeServer()
    return RedisTaskStore("redis://localhost", client_factory=lambda _url: _FakeAsyncRedis(server=server), **kwargs)


@pytest.fixture(params=["memory", "redis"])
def store(request, tmp_path):
    """同一组行为用例在 memory / redis 两种实现上各跑一遍。"""
    if request.param == "redis":
        return _make_redis_store()
    return MemoryTaskStore(str(tmp_path / "tasks.db"))


async def _backdate(store, task_id: str, seconds: float) -> None:
    """把任务的 updated_at 回拨，模拟「心跳已停止」的陈旧任务（S2 陈旧判定用例）。"""
    task = await store.get(task_id)
    assert task is not None
    task.updated_at = time.time() - seconds
    if isinstance(store, MemoryTaskStore):
        with store._lock:
            store._upsert_row(task)
    else:
        await store._set(store._key(task_id), task)


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


def test_updated_at_roundtrip_preserved(store):
    """updated_at 必须往返保真：S2 陈旧判定（心跳）依赖它，不能被 created_at 覆盖。"""
    task_id = asyncio.run(store.create(VIDEO))
    asyncio.run(store.update(task_id, progress=10))
    task = asyncio.run(store.get(task_id))
    assert task.updated_at >= task.created_at


def test_attempts_defaults_to_zero_and_roundtrips(store):
    """attempts（S2 重试计数）默认 0，写入后随 JSON/SQLite 往返保真。"""
    task_id = asyncio.run(store.create(VIDEO))
    assert asyncio.run(store.get(task_id)).attempts == 0

    asyncio.run(store.update(task_id, attempts=2))
    assert asyncio.run(store.get(task_id)).attempts == 2


def test_init_marks_stale_processing_tasks_interrupted(store):
    """启动恢复：陈旧的 processing 任务标记 interrupted，已完成不受影响。"""
    processing_id = asyncio.run(store.create(VIDEO))
    done_id = asyncio.run(store.create(VIDEO))
    asyncio.run(store.update(done_id, status="completed"))
    asyncio.run(_backdate(store, processing_id, 3600))

    asyncio.run(store.init())  # 模拟进程重启

    assert asyncio.run(store.get(processing_id)).status == "interrupted"
    assert asyncio.run(store.get(done_id)).status == "completed"


def test_init_without_recovery_keeps_processing_tasks(store):
    """Worker 语义：init(recover_interrupted=False) 只初始化，绝不改写任务状态。

    Serverless 下每个 Worker 实例冷启动都会跑 init，若沿用「全量打 interrupted」，
    会把其他实例正在跑的任务全部误杀（S2-P0 核心缺陷）。
    """
    task_id = asyncio.run(store.create(VIDEO))
    asyncio.run(_backdate(store, task_id, 3600))

    asyncio.run(store.init(recover_interrupted=False))

    assert asyncio.run(store.get(task_id)).status == "processing"


def test_init_is_idempotent(store):
    task_id = asyncio.run(store.create(VIDEO))
    asyncio.run(_backdate(store, task_id, 3600))
    asyncio.run(store.init())
    asyncio.run(store.init())  # 二次 init 不重复处理
    assert asyncio.run(store.get(task_id)).status == "interrupted"


# ---- TTL（S2-P0）----

async def _ttl_of(store: RedisTaskStore, task_id: str) -> int:
    return await store._client().ttl(store._key(task_id))


def test_redis_create_sets_ttl():
    """create 必须带 TTL：任务 JSON 含完整文字稿，无 TTL 会无限增长。"""
    store = _make_redis_store(ttl_seconds=600)
    task_id = asyncio.run(store.create(VIDEO))
    ttl = asyncio.run(_ttl_of(store, task_id))
    assert 0 < ttl <= 600


def test_redis_update_refreshes_ttl():
    """update 也要续期（滑动 TTL）：否则长任务跑到一半 key 就过期了。"""
    store = _make_redis_store(ttl_seconds=600)
    task_id = asyncio.run(store.create(VIDEO))
    asyncio.run(store.update(task_id, progress=10))
    ttl = asyncio.run(_ttl_of(store, task_id))
    assert 0 < ttl <= 600


def test_redis_ttl_zero_disables_expiry():
    """TASK_TTL_SECONDS=0 → 不设 TTL（保留旧行为，便于需要永久留存的部署）。"""
    store = _make_redis_store(ttl_seconds=0)
    task_id = asyncio.run(store.create(VIDEO))
    assert asyncio.run(_ttl_of(store, task_id)) == -1  # -1 = 无过期时间


# ---- 陈旧判定（S2-P0）----

def test_redis_init_keeps_fresh_processing_tasks():
    """心跳正常的 processing 任务不能被 init 判死（多实例/冷启动场景的核心约束）。"""
    store = _make_redis_store()
    task_id = asyncio.run(store.create(VIDEO))  # updated_at = 刚刚
    asyncio.run(store.init())
    assert asyncio.run(store.get(task_id)).status == "processing"


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
