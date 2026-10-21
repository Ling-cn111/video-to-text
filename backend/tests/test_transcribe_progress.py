"""进度上报（S2-P0）：节流写入 + 心跳，避免长阻塞步骤被判为僵尸任务。"""
import threading
import time

import pytest

from app.services import transcribe as transcribe_module
from app.services.transcribe import PROGRESS_ASR_SPAN, PROGRESS_ASR_START, PROGRESS_STEP, ProgressReporter


@pytest.fixture()
def writes(monkeypatch):
    """拦截 update_task，记录每次写入的字段（不碰真实存储）。"""
    recorded: list[dict] = []

    def _fake_update(task_id, **fields):
        recorded.append({"task_id": task_id, **fields})

    monkeypatch.setattr(transcribe_module, "update_task", _fake_update)
    return recorded


def test_reporter_writes_on_stage_change(writes):
    reporter = ProgressReporter("t1", heartbeat_seconds=0)
    reporter.set("parse_link", 5)
    reporter.set("extract_audio", 15)
    reporter.close()
    assert [w["stage"] for w in writes] == ["parse_link", "extract_audio"]


def test_reporter_throttles_small_progress_jumps(writes):
    """进度涨幅 < PROGRESS_STEP 且阶段不变 → 不写（ASR 分段回调的主要开销来源）。"""
    reporter = ProgressReporter("t1", heartbeat_seconds=0)
    reporter.set("asr", 40)
    reporter.bump(41)
    reporter.bump(43)
    reporter.bump(44)
    reporter.bump(45)  # 涨幅 5，达标
    reporter.close()
    assert [w["progress"] for w in writes] == [40, 45]


def test_reporter_writes_on_same_progress_after_stage_change(writes):
    """阶段变化即使进度相同也要写（前端据此切换文案）。"""
    reporter = ProgressReporter("t1", heartbeat_seconds=0)
    reporter.set("parse_link", 5)
    reporter.set("extract_audio", 5)
    reporter.close()
    assert len(writes) == 2


def test_reporter_noop_after_close(writes):
    """收尾后必须停止上报：否则心跳会把 completed/failed 的进度覆盖回去。"""
    reporter = ProgressReporter("t1", heartbeat_seconds=0)
    reporter.set("asr", 40)
    reporter.close()
    reporter.set("asr", 90)
    reporter.bump(99)
    assert len(writes) == 1


def test_reporter_heartbeat_keeps_task_fresh(writes):
    """长阻塞步骤（无进度变化）期间，心跳必须定期补写，否则 reaper 会误杀。"""
    reporter = ProgressReporter("t1", heartbeat_seconds=0.05)
    reporter.set("extract_audio", 15)
    time.sleep(0.3)
    reporter.close()
    # 首次写入 + 至少 2 次心跳
    assert len(writes) >= 3
    assert all(w["stage"] == "extract_audio" and w["progress"] == 15 for w in writes)


def test_reporter_heartbeat_stops_after_close(writes):
    reporter = ProgressReporter("t1", heartbeat_seconds=0.05)
    reporter.set("asr", 40)
    reporter.close()
    time.sleep(0.2)
    assert len(writes) == 1
    assert not any(t.name.startswith("task-heartbeat-") and t.is_alive() for t in threading.enumerate())


def test_reporter_swallows_storage_errors(monkeypatch):
    """进度写入失败不能打断转写主流程。"""
    monkeypatch.setattr(
        transcribe_module, "update_task", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("redis down"))
    )
    reporter = ProgressReporter("t1", heartbeat_seconds=0)
    reporter.set("parse_link", 5)  # 不抛
    reporter.close()


def test_asr_progress_maps_into_span(writes):
    """ASR 回调比例 → 全局进度（40..95），且按 PROGRESS_STEP 节流。"""
    reporter = ProgressReporter("t1", heartbeat_seconds=0)
    reporter.set("asr", PROGRESS_ASR_START)
    for ratio in (0.0, 0.01, 0.05, 0.10, 0.50, 1.0):
        reporter.bump(PROGRESS_ASR_START + int(ratio * PROGRESS_ASR_SPAN))
    reporter.close()
    progresses = [w["progress"] for w in writes]
    assert progresses[0] == PROGRESS_ASR_START
    assert progresses[-1] == PROGRESS_ASR_START + PROGRESS_ASR_SPAN
    assert progresses == sorted(progresses)
    assert all(b - a >= PROGRESS_STEP for a, b in zip(progresses, progresses[1:]))
