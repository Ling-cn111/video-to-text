"""任务分发抽象（S2-P1）：ENQUEUE_MODE 各模式行为。

重点保证 inline 模式与改造前完全一致：
- 有 background_tasks 时注册到 FastAPI（响应返回后执行），不在请求内阻塞
- 无 background_tasks 时同步执行（脚本/CLI/单测复用）
- 传给 pipeline 的 6 个参数与作业体逐一相等
"""
import pytest

from app.schemas import VideoInfo
from app.services import dispatcher as dispatcher_module
from app.services import transcribe as transcribe_module
from app.services.dispatcher import (
    DispatcherNotImplementedError,
    InlineDispatcher,
    TranscribeJob,
    _build_dispatcher,
    enqueue,
)

VIDEO = VideoInfo(title="测试视频", cover="https://i1.hdslb.com/c.jpg", duration=135, platform="bilibili", videoId="BV1GJ411x7h7")


def _job(**overrides) -> TranscribeJob:
    data = {
        "task_id": "task-1",
        "url": "https://www.bilibili.com/video/BV1GJ411x7h7",
        "video": VIDEO,
        "prefer_subtitles": True,
        "hotwords": ["转写", "ASR"],
        "engine": "cloud",
    }
    data.update(overrides)
    return TranscribeJob(**data)


class _FakeBackgroundTasks:
    """记录 add_task 的注册内容，但不执行（模拟 FastAPI 的行为）。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def add_task(self, func, *args, **kwargs) -> None:
        self.calls.append((func, args, kwargs))


# ---- 作业体序列化（P2/P3 的跨进程投递格式）----

def test_job_roundtrip():
    job = _job()
    restored = TranscribeJob.from_dict(job.to_dict())
    assert restored == job


def test_job_from_dict_tolerates_missing_optionals():
    """旧事件/精简事件缺字段时走默认值，不炸。"""
    job = TranscribeJob.from_dict({"task_id": "t", "url": "https://example.test/v"})
    assert job.task_id == "t" and job.url == "https://example.test/v"
    assert job.video is None
    assert job.prefer_subtitles is True
    assert job.hotwords is None and job.engine is None


def test_job_to_dict_is_json_serializable():
    import json

    payload = json.loads(json.dumps(_job().to_dict()))
    assert payload["video"]["videoId"] == "BV1GJ411x7h7"
    assert payload["hotwords"] == ["转写", "ASR"]


# ---- inline：注册到 BackgroundTasks（生产路径）----

def test_inline_registers_pipeline_on_background_tasks(monkeypatch):
    calls = []
    monkeypatch.setattr(
        transcribe_module,
        "run_transcription_pipeline",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    tasks = _FakeBackgroundTasks()

    InlineDispatcher().enqueue(_job(), background_tasks=tasks)

    assert len(tasks.calls) == 1
    func, args, kwargs = tasks.calls[0]
    assert func is transcribe_module.run_transcription_pipeline
    assert args == ("task-1", "https://www.bilibili.com/video/BV1GJ411x7h7", VIDEO, True, ["转写", "ASR"], "cloud")
    assert kwargs == {}
    assert calls == []  # 未在请求内执行（由 Starlette 在响应后调用）


def test_inline_without_background_tasks_runs_synchronously(monkeypatch):
    calls = []
    monkeypatch.setattr(
        transcribe_module,
        "run_transcription_pipeline",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    InlineDispatcher().enqueue(_job())

    assert len(calls) == 1
    assert calls[0][0][0] == "task-1"


# ---- 模块级 enqueue 走当前分发器 ----

def test_enqueue_uses_current_dispatcher(monkeypatch):
    """模块级 enqueue 必须委托 _DISPATCHER（测试可替换，生产按 ENQUEUE_MODE 构造）。"""
    seen = []
    monkeypatch.setattr(
        dispatcher_module,
        "_DISPATCHER",
        type("_Spy", (), {"mode": "spy", "enqueue": lambda self, job, background_tasks=None: seen.append(job)})(),
    )

    enqueue(_job())

    assert len(seen) == 1 and seen[0].task_id == "task-1"


def test_enqueue_inline_mode_triggers_pipeline(monkeypatch):
    """ENQUEUE_MODE=inline 时，enqueue 会正确触发 run_transcription_pipeline。"""
    calls = []
    monkeypatch.setattr(
        transcribe_module,
        "run_transcription_pipeline",
        lambda *args, **kwargs: calls.append(args),
    )
    monkeypatch.setattr(dispatcher_module, "_DISPATCHER", _build_dispatcher("inline"))

    enqueue(_job())

    assert len(calls) == 1
    assert calls[0] == ("task-1", "https://www.bilibili.com/video/BV1GJ411x7h7", VIDEO, True, ["转写", "ASR"], "cloud")


# ---- fc / mns / redis：显式未实现 ----

@pytest.mark.parametrize("mode", ["fc", "mns", "redis"])
def test_unimplemented_modes_raise(monkeypatch, mode):
    """fc/mns/redis 当前是显式未实现：必须报错，绝不静默丢任务。"""
    monkeypatch.setattr(dispatcher_module, "_DISPATCHER", _build_dispatcher(mode))

    with pytest.raises(DispatcherNotImplementedError, match=mode):
        enqueue(_job())


def test_dispatcher_not_implemented_is_not_implemented_error():
    """继承 NotImplementedError：调用方可用标准异常类型兜底。"""
    assert issubclass(DispatcherNotImplementedError, NotImplementedError)


def test_build_dispatcher_modes():
    assert isinstance(_build_dispatcher("inline"), InlineDispatcher)
    assert _build_dispatcher("fc").mode == "fc"
    assert _build_dispatcher("mns").mode == "mns"
    assert _build_dispatcher("redis").mode == "redis"


def test_build_dispatcher_unknown_mode_degrades_to_inline(caplog):
    """配错模式不能直接 500：告警后降级 inline（与 _build_store 同款兜底）。"""
    with caplog.at_level("WARNING"):
        built = _build_dispatcher("nope")
    assert isinstance(built, InlineDispatcher)
    assert "ENQUEUE_MODE" in caplog.text


def test_build_dispatcher_defaults_to_inline():
    """空值/缺省按 inline 处理。"""
    assert isinstance(_build_dispatcher(""), InlineDispatcher)
