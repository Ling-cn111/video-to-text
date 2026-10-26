"""任务分发抽象（S2-P1/P3）：ENQUEUE_MODE 各模式行为。

重点保证 inline 模式与改造前完全一致：
- 有 background_tasks 时注册到 FastAPI（响应返回后执行），不在请求内阻塞
- 无 background_tasks 时同步执行（脚本/CLI/单测复用）
- 传给 pipeline 的 6 个参数与作业体逐一相等

fc 模式（P3）用 httpx.MockTransport 验证「发什么请求」，不联网：
- 端点拼接、异步头、token 头、body 为完整作业体
- 202/200 视为成功；其余状态码与网络错误抛 DispatchError
- 未配置 WORKER_URL 时抛 DispatchError（不静默丢任务）
"""
import json

import httpx
import pytest

from app.schemas import VideoInfo
from app.services import dispatcher as dispatcher_module
from app.services import transcribe as transcribe_module
from app.services.dispatcher import (
    DispatchError,
    DispatcherNotImplementedError,
    FcDispatcher,
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
    # byok_key 走关键字：它是 on_heartbeat 之后新增的第 7 个形参，位置传递会串位
    assert kwargs == {"byok_key": None}
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

@pytest.mark.parametrize("mode", ["mns", "redis"])
def test_unimplemented_modes_raise(monkeypatch, mode):
    """mns/redis 当前是显式未实现：必须报错，绝不静默丢任务。"""
    monkeypatch.setattr(dispatcher_module, "_DISPATCHER", _build_dispatcher(mode))

    with pytest.raises(DispatcherNotImplementedError, match=mode):
        enqueue(_job())


def test_dispatcher_not_implemented_is_not_implemented_error():
    """继承 NotImplementedError：调用方可用标准异常类型兜底。"""
    assert issubclass(DispatcherNotImplementedError, NotImplementedError)


def test_build_dispatcher_modes():
    assert isinstance(_build_dispatcher("inline"), InlineDispatcher)
    assert isinstance(_build_dispatcher("fc"), FcDispatcher)
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


# ---- fc：HTTP 触发器异步投递（P3）----

WORKER_URL = "https://video-to-text-worker.abc123.cn-hangzhou.fcapp.run"


def _fc(handler, **kwargs) -> FcDispatcher:
    """构造注入了 MockTransport 的 FcDispatcher（不联网）。"""
    kwargs.setdefault("url", WORKER_URL)
    kwargs.setdefault("token", "")
    return FcDispatcher(transport=httpx.MockTransport(handler), **kwargs)


def _record(seen: list[httpx.Request], status_code: int = 202):
    """返回一个记录请求并固定应答的 handler。"""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status_code, headers={"X-Fc-Request-Id": "req-1"}, json={"ok": True})

    return handler


def test_fc_endpoint_joins_url_and_path():
    dispatcher = FcDispatcher(url=WORKER_URL)
    assert dispatcher.endpoint == f"{WORKER_URL}/internal/worker/transcribe"


def test_fc_endpoint_tolerates_extra_slashes():
    """配置里多写/少写斜杠都要能拼对（运维最容易犯的错）。"""
    assert FcDispatcher(url=WORKER_URL + "/").endpoint == f"{WORKER_URL}/internal/worker/transcribe"
    assert FcDispatcher(url=WORKER_URL, path="internal/worker/transcribe").endpoint == (
        f"{WORKER_URL}/internal/worker/transcribe"
    )
    assert FcDispatcher(url=WORKER_URL, path="").endpoint == WORKER_URL


def test_fc_posts_async_header_and_full_job():
    """必须带异步头（否则会同步跑完阻塞 API 请求），且 body 是完整作业体。"""
    seen: list[httpx.Request] = []
    _fc(_record(seen)).enqueue(_job())

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == f"{WORKER_URL}/internal/worker/transcribe"
    assert request.headers["X-Fc-Invocation-Type"] == "Async"
    assert request.headers["Content-Type"] == "application/json"

    body = json.loads(request.content)
    assert body["task_id"] == "task-1"
    assert body["url"] == "https://www.bilibili.com/video/BV1GJ411x7h7"
    assert body["video"]["videoId"] == "BV1GJ411x7h7"
    assert body["hotwords"] == ["转写", "ASR"]
    assert body["engine"] == "cloud"


def test_fc_body_is_utf8_not_ascii_escaped():
    """ensure_ascii=False：中文直传，body 体积更小且便于日志排查。"""
    seen: list[httpx.Request] = []
    _fc(_record(seen)).enqueue(_job())

    assert "转写".encode() in seen[0].content


def test_fc_sends_worker_token_when_configured():
    seen: list[httpx.Request] = []
    _fc(_record(seen), token="s3cret").enqueue(_job())

    assert seen[0].headers["X-Worker-Token"] == "s3cret"
    # 兼容将来把触发器改成 Bearer 认证
    assert seen[0].headers["Authorization"] == "Bearer s3cret"


def test_fc_omits_token_headers_when_unconfigured():
    seen: list[httpx.Request] = []
    _fc(_record(seen)).enqueue(_job())

    assert "X-Worker-Token" not in seen[0].headers
    assert "Authorization" not in seen[0].headers


def test_fc_reads_worker_token_at_call_time(monkeypatch):
    """默认 token=None：每次投递读模块级 WORKER_TOKEN。

    这样改环境变量/测试 monkeypatch 都不必重建分发器（_DISPATCHER 是模块级单例）。
    """
    seen: list[httpx.Request] = []
    dispatcher = _fc(_record(seen), token=None)

    monkeypatch.setattr(dispatcher_module, "WORKER_TOKEN", "late-bound")
    dispatcher.enqueue(_job())

    assert seen[0].headers["X-Worker-Token"] == "late-bound"


def test_fc_explicit_empty_token_disables_auth_headers(monkeypatch):
    """显式传 ""：即使全局配了 token 也不带（用于连本地无鉴权的 worker）。"""
    seen: list[httpx.Request] = []
    monkeypatch.setattr(dispatcher_module, "WORKER_TOKEN", "global-token")

    _fc(_record(seen), token="").enqueue(_job())

    assert "X-Worker-Token" not in seen[0].headers


@pytest.mark.parametrize("status", [200, 202])
def test_fc_accepts_200_and_202(status):
    """202 = FC 异步受理；200 = 直连 worker 同步跑完（本地联调）。"""
    _fc(_record([], status_code=status)).enqueue(_job())


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 502])
def test_fc_raises_on_non_2xx(status):
    with pytest.raises(DispatchError, match=str(status)):
        _fc(_record([], status_code=status)).enqueue(_job())


def test_fc_error_message_truncates_body():
    """错误信息要能进日志，但不能把整页 HTML 灌进去。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="x" * 5000)

    with pytest.raises(DispatchError) as excinfo:
        _fc(handler).enqueue(_job())
    assert len(str(excinfo.value)) < 400


def test_fc_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(DispatchError, match="投递失败"):
        _fc(handler).enqueue(_job())


def test_fc_raises_on_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(DispatchError, match="投递失败"):
        _fc(handler).enqueue(_job())


def test_fc_raises_dispatch_error_on_non_ascii_token():
    """HTTP 头只能是 ASCII：token 配成中文时 httpx 抛 UnicodeEncodeError。

    必须转成 DispatchError（→ 任务 failed + 500 且日志可读），否则会变成
    一个看不出原因的 500。
    """
    with pytest.raises(DispatchError, match="WORKER_TOKEN"):
        _fc(_record([]), token="口令").enqueue(_job())


def test_fc_raises_when_url_missing():
    """未配置 WORKER_URL：不能静默丢任务，必须显式报错。"""
    dispatcher = FcDispatcher(url="", transport=httpx.MockTransport(_record([])))

    with pytest.raises(DispatchError, match="WORKER_URL"):
        dispatcher.enqueue(_job())


def test_fc_ignores_background_tasks():
    """异步由 FC 队列承担，不能再往 BackgroundTasks 里塞（会重复执行）。"""
    seen: list[httpx.Request] = []
    tasks = _FakeBackgroundTasks()

    _fc(_record(seen)).enqueue(_job(), background_tasks=tasks)

    assert len(seen) == 1
    assert tasks.calls == []


def test_fc_enqueue_goes_through_module_entry(monkeypatch):
    """模块级 enqueue 在 ENQUEUE_MODE=fc 下也必须走 FC 投递。"""
    seen: list[httpx.Request] = []
    monkeypatch.setattr(
        dispatcher_module, "_DISPATCHER", _fc(_record(seen), token="s3cret")
    )

    enqueue(_job())

    assert len(seen) == 1
    assert seen[0].headers["X-Fc-Invocation-Type"] == "Async"


def test_fc_does_not_call_pipeline_locally(monkeypatch):
    """投递成功 ≠ 本地执行：pipeline 绝不能在 API 进程里跑。"""
    calls = []
    monkeypatch.setattr(
        transcribe_module, "run_transcription_pipeline", lambda *a, **k: calls.append(a)
    )

    _fc(_record([])).enqueue(_job())

    assert calls == []

