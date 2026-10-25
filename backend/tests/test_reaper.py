"""僵尸任务清理（S2-P4 Reaper）：判定逻辑 + 并发安全 + 双存储一致性。

同一组用例参数化跑在 memory 与 fakeredis 上——reaper 的语义不该随
TASK_STORE 切换而变，这是 S1-A 定下的规矩。

重点覆盖「不误杀」：陈旧判定必须同时满足「status=processing」与
「updated_at 超过阈值」，且写入前要做二次校验（扫描与写入之间可能刚被心跳刷新）。
"""
import asyncio
import time

import pytest
import redis.asyncio as redis_async

fakeredis = pytest.importorskip("fakeredis")
fakeredis_aioredis = pytest.importorskip("fakeredis.aioredis")

from app.schemas import VideoInfo
from app.services.reaper import (
    TASK_TIMEOUT_CODE,
    TASK_TIMEOUT_MESSAGE,
    reap_once,
    reap_once_sync,
)
from app.services.tasks import REAPER_LOCK_ID, MemoryTaskStore, RedisTaskStore

VIDEO = VideoInfo(
    title="Reaper 测试视频",
    cover="https://i1.hdslb.com/c.jpg",
    duration=135,
    platform="bilibili",
    videoId="BV1GJ411x7h7",
)


class _FakeAsyncRedis(fakeredis_aioredis.FakeAsyncRedisMixin, redis_async.Redis):
    """fakeredis 2.30+：Mixin 必须先于 redis.asyncio.Redis（MRO 顺序）。"""


def _make_redis_store(**kwargs) -> RedisTaskStore:
    server = fakeredis.FakeServer()
    return RedisTaskStore(
        "redis://localhost",
        client_factory=lambda _url: _FakeAsyncRedis(server=server),
        **kwargs,
    )


@pytest.fixture(params=["memory", "redis"])
def store(request, tmp_path):
    if request.param == "redis":
        return _make_redis_store()
    return MemoryTaskStore(str(tmp_path / "tasks.db"))


async def _backdate(store, task_id: str, seconds: float, *, started: bool | None = None) -> None:
    """把 updated_at 回拨，模拟「心跳已停止」的僵尸任务。

    `started=True` 置上 started_at（表示 Worker 已在跑）；`started=False` 清空
    （表示还在排队）；`None` 保持原样。
    """
    task = await store.get(task_id)
    assert task is not None
    task.updated_at = time.time() - seconds
    if started is not None:
        task.started_at = task.updated_at if started else None
    if isinstance(store, MemoryTaskStore):
        with store._lock:
            store._upsert_row(task)
    else:
        await store._set(store._key(task_id), task)


async def _create(store, **fields) -> str:
    task_id = await store.create(VIDEO)
    if fields:
        await store.update(task_id, **fields)
    return task_id


# ---- 判定：陈旧 processing → failed ----

def test_reaps_stale_processing_task(store):
    task_id = asyncio.run(_create(store))
    asyncio.run(_backdate(store, task_id, 3600, started=True))

    result = asyncio.run(reap_once(store, stale_seconds=600, lock_seconds=30))

    assert result.status == "ok"
    assert result.scanned == 1
    assert result.reaped == 1
    assert result.task_ids == [task_id]

    task = asyncio.run(store.get(task_id))
    assert task.status == "failed"
    assert task.error == {"code": TASK_TIMEOUT_CODE, "message": TASK_TIMEOUT_MESSAGE}


def test_timeout_error_code_and_message_are_stable(store):
    """错误码与文案是前后端契约（src/lib/types.ts 的 ApiErrorCode 必须含 TASK_TIMEOUT）。"""
    assert TASK_TIMEOUT_CODE == "TASK_TIMEOUT"
    assert TASK_TIMEOUT_MESSAGE == "任务处理超时，可能因服务器资源受限中断，请重试"

    task_id = asyncio.run(_create(store))
    asyncio.run(_backdate(store, task_id, 3600, started=True))
    asyncio.run(reap_once(store, stale_seconds=600))
    assert asyncio.run(store.get(task_id)).error["code"] == "TASK_TIMEOUT"


# ---- 不误杀 ----

def test_fresh_processing_task_survives(store):
    """心跳正常：可能有实例正在处理，绝不能动。"""
    task_id = asyncio.run(_create(store))

    result = asyncio.run(reap_once(store, stale_seconds=600))

    assert result.scanned == 1
    assert result.reaped == 0
    assert result.task_ids == []
    assert asyncio.run(store.get(task_id)).status == "processing"


def test_stale_but_below_threshold_survives(store):
    task_id = asyncio.run(_create(store))
    asyncio.run(_backdate(store, task_id, 300, started=True))  # 阈值 600，未到

    result = asyncio.run(reap_once(store, stale_seconds=600))

    assert result.reaped == 0
    assert asyncio.run(store.get(task_id)).status == "processing"


# ---- 排队中的任务：两档阈值（P1-1 修复误杀）----

def test_queued_task_is_not_reaped_before_queued_threshold(store):
    """P1-1 回归：started_at 为空 = 还在排队，没有心跳，必须用更宽的阈值。

    旧实现只看 updated_at，排队超过 TASK_STALE_SECONDS（600s）就被判僵尸——
    用户看到「处理超时」，而 Worker 根本没领取过这个任务。
    """
    task_id = asyncio.run(_create(store))
    asyncio.run(_backdate(store, task_id, 900, started=False))  # 900s > 600s，但 < 1800s

    result = asyncio.run(reap_once(store, stale_seconds=600))

    assert result.reaped == 0
    assert asyncio.run(store.get(task_id)).status == "processing"


def test_queued_task_is_reaped_past_queued_threshold(store):
    """排队阈值不是无限大：真卡死（超过 REAPER_QUEUED_STALE_SECONDS）仍然要收。"""
    task_id = asyncio.run(_create(store))
    asyncio.run(_backdate(store, task_id, 3600, started=False))

    result = asyncio.run(reap_once(store, stale_seconds=600))

    assert result.reaped == 1
    assert asyncio.run(store.get(task_id)).status == "failed"


def test_started_task_uses_strict_threshold(store):
    """对照：一旦 Worker 真的开始跑（started_at 有值），600s 就够判僵尸。"""
    task_id = asyncio.run(_create(store))
    asyncio.run(_backdate(store, task_id, 900, started=True))

    result = asyncio.run(reap_once(store, stale_seconds=600))

    assert result.reaped == 1
    assert asyncio.run(store.get(task_id)).status == "failed"


def test_queued_threshold_never_shrinks_below_stale(store):
    """配置把排队阈值调得比常规阈值还小时取 max——不能反而更激进地杀排队任务。"""
    task_id = asyncio.run(_create(store))
    asyncio.run(_backdate(store, task_id, 900, started=False))

    result = asyncio.run(reap_once(store, stale_seconds=3600))

    assert result.reaped == 0
    assert asyncio.run(store.get(task_id)).status == "processing"


@pytest.mark.parametrize("terminal", ["completed", "failed", "interrupted"])
def test_terminal_tasks_are_ignored(store, terminal):
    """只收 processing：已完成/已失败的任务不该出现在扫描结果里，更不该被改写。"""
    task_id = asyncio.run(_create(store, status=terminal))
    asyncio.run(_backdate(store, task_id, 3600, started=True))

    result = asyncio.run(reap_once(store, stale_seconds=600))

    assert result.scanned == 0
    assert result.reaped == 0
    assert asyncio.run(store.get(task_id)).status == terminal


def test_second_run_is_idempotent(store):
    """重复执行只会看到已 failed 的任务并跳过（FC 定时器每分钟都可能触发）。"""
    task_id = asyncio.run(_create(store))
    asyncio.run(_backdate(store, task_id, 3600, started=True))

    first = asyncio.run(reap_once(store, stale_seconds=600))
    second = asyncio.run(reap_once(store, stale_seconds=600))

    assert first.reaped == 1
    assert second.scanned == 0 and second.reaped == 0


def test_list_processing_tasks_excludes_derived_keys(store):
    """扫描必须跳过 task:lock:* 之类的派生 key（Redis 实现的实际坑）。"""
    task_id = asyncio.run(_create(store))
    token = asyncio.run(store.acquire(task_id, 60))
    assert token is not None

    processing = asyncio.run(store.list_processing_tasks())

    assert [task.task_id for task in processing] == [task_id]


# ---- 并发安全 ----

def test_reaper_lock_prevents_concurrent_scan(store):
    """另一个 reaper 持锁时直接 skipped，不扫描也不写。"""
    task_id = asyncio.run(_create(store))
    asyncio.run(_backdate(store, task_id, 3600, started=True))
    token = asyncio.run(store.acquire(REAPER_LOCK_ID, 60))
    assert token is not None

    try:
        result = asyncio.run(reap_once(store, stale_seconds=600))
        assert result.status == "skipped"
        assert result.reason == "locked"
        assert result.scanned == 0
        assert asyncio.run(store.get(task_id)).status == "processing"
    finally:
        asyncio.run(store.release(REAPER_LOCK_ID, token))


def test_reaper_lock_is_released_after_run(store):
    """正常跑完必须放锁，否则下一轮（5 分钟后）会一直 skipped。"""
    asyncio.run(reap_once(store, stale_seconds=600))
    token = asyncio.run(store.acquire(REAPER_LOCK_ID, 60))
    assert token is not None, "reaper 跑完后应释放互斥锁"
    asyncio.run(store.release(REAPER_LOCK_ID, token))


def test_reaper_lock_is_released_on_error(store, monkeypatch):
    """扫描中途抛异常也必须放锁（finally），否则 reaper 永久瘫痪。"""
    async def _boom(self):
        raise RuntimeError("存储抖动")

    monkeypatch.setattr(type(store), "list_processing_tasks", _boom)

    with pytest.raises(RuntimeError):
        asyncio.run(reap_once(store, stale_seconds=600))

    token = asyncio.run(store.acquire(REAPER_LOCK_ID, 60))
    assert token is not None, "异常路径必须释放互斥锁"
    asyncio.run(store.release(REAPER_LOCK_ID, token))


def test_fail_if_stale_rechecks_before_writing(store):
    """扫描与写入之间心跳刚刷新 → 二次校验必须拦住，不能盲写。"""
    task_id = asyncio.run(_create(store))
    asyncio.run(_backdate(store, task_id, 3600, started=True))

    # 模拟「扫描之后、写入之前」有一次心跳把 updated_at 刷新到当前
    asyncio.run(store.update(task_id, progress=50))

    changed = asyncio.run(
        store.fail_if_stale(task_id, 600, {"code": TASK_TIMEOUT_CODE, "message": TASK_TIMEOUT_MESSAGE})
    )

    assert changed is False
    assert asyncio.run(store.get(task_id)).status == "processing"


def test_fail_if_stale_skips_non_processing(store):
    task_id = asyncio.run(_create(store, status="completed"))
    asyncio.run(_backdate(store, task_id, 3600, started=True))

    changed = asyncio.run(store.fail_if_stale(task_id, 600, {"code": "TASK_TIMEOUT", "message": "x"}))

    assert changed is False
    assert asyncio.run(store.get(task_id)).status == "completed"


def test_fail_if_stale_skips_unknown_task(store):
    changed = asyncio.run(store.fail_if_stale("no-such-task", 600, {"code": "TASK_TIMEOUT", "message": "x"}))
    assert changed is False


# ---- dry-run ----

def test_dry_run_reports_without_writing(store):
    task_id = asyncio.run(_create(store))
    asyncio.run(_backdate(store, task_id, 3600, started=True))

    result = asyncio.run(reap_once(store, stale_seconds=600, dry_run=True))

    assert result.reaped == 1
    assert result.task_ids == [task_id]
    assert asyncio.run(store.get(task_id)).status == "processing"


# ---- 结果序列化 ----

def test_result_to_dict_omits_empty_fields(store):
    """端点响应体要干净：skipped 才带 reason，没有命中时不带 task_ids。"""
    asyncio.run(_create(store))
    payload = asyncio.run(reap_once(store, stale_seconds=600)).to_dict()

    assert payload == {"status": "ok", "scanned": 1, "reaped": 0}
    assert "reason" not in payload
    assert "task_ids" not in payload


def test_skipped_result_carries_reason(store):
    token = asyncio.run(store.acquire(REAPER_LOCK_ID, 60))
    try:
        payload = asyncio.run(reap_once(store, stale_seconds=600)).to_dict()
        assert payload["status"] == "skipped"
        assert payload["reason"] == "locked"
    finally:
        asyncio.run(store.release(REAPER_LOCK_ID, token))


# ---- 同步入口（本地 CLI 用）----

def test_sync_entry_reaps_via_bridge(store, monkeypatch):
    """脚本入口必须走常驻事件循环桥接（不能 asyncio.run，否则泄漏 Redis 连接池）。"""
    from app.services import tasks as tasks_module

    monkeypatch.setattr(tasks_module, "_STORE", store)

    task_id = asyncio.run(_create(store))
    asyncio.run(_backdate(store, task_id, 3600, started=True))

    result = reap_once_sync(stale_seconds=600)

    assert result.reaped == 1
    assert asyncio.run(store.get(task_id)).status == "failed"
