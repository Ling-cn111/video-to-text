"""Worker 应用（S2-P2）：独立 ASGI 入口的端到端行为。

覆盖：正常执行、幂等领取（锁冲突）、状态跳过、业务失败回 200、
基础设施异常回 5xx、事件体宽容解包、鉴权、初始化不误杀、attempts 递增。

测试直接用 TestClient 打 worker_app（不经过 API 应用），
默认 TASK_STORE=memory，无需 Redis。
"""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services import tasks as tasks_module
from app.services import transcribe as transcribe_module
from app.services.dispatcher import FcDispatcher, TranscribeJob
from app.worker_app import INVOKE_PATH, REAP_PATH, WORKER_PATH, app as worker_app

client = TestClient(worker_app)

VIDEO = {
    "title": "Worker 测试视频",
    "cover": "https://i1.hdslb.com/bfs/archive/test.jpg",
    "duration": 135,
    "platform": "bilibili",
    "videoId": "BV1GJ411x7h7",
}
URL = "https://www.bilibili.com/video/BV1GJ411x7h7"


@pytest.fixture()
def store():
    return tasks_module.get_task_store()


@pytest.fixture()
def new_task(store):
    """建一条 processing 任务（等价于 API 侧 POST 之后、分发之前的状态）。"""
    from app.schemas import VideoInfo

    task_id = asyncio.run(store.create(VideoInfo(**VIDEO)))
    return task_id


@pytest.fixture()
def subtitle_ok(monkeypatch):
    """字幕快路径：秒级完成，不触网、不跑 ASR。"""
    monkeypatch.setattr(
        transcribe_module,
        "try_extract_subtitle_transcript",
        lambda url: ([{"time": 1.0, "text": "Worker 跑通"}], "subtitle_ai"),
    )


@pytest.fixture()
def pipeline_calls(monkeypatch):
    """把 pipeline 换成只记录调用的桩（验证「不该跑的时候没跑」）。"""
    calls: list[tuple] = []

    def _fake_pipeline(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(transcribe_module, "run_transcription_pipeline", _fake_pipeline)
    return calls


def _job(task_id: str, **overrides) -> dict:
    return TranscribeJob(task_id=task_id, url=URL, **overrides).to_dict()


# ---- 正常路径 ----

def test_worker_completes_task(new_task, subtitle_ok, store):
    response = client.post(WORKER_PATH, json=_job(new_task))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "task_id": new_task, "task_status": "completed"}

    task = asyncio.run(store.get(new_task))
    assert task.status == "completed"
    assert task.plain_text == "Worker 跑通"
    assert task.transcript_source == "subtitle_ai"


def test_worker_increments_attempts(new_task, subtitle_ok, store):
    assert asyncio.run(store.get(new_task)).attempts == 0
    client.post(WORKER_PATH, json=_job(new_task))
    assert asyncio.run(store.get(new_task)).attempts == 1


def test_worker_passes_job_fields_to_pipeline(new_task, pipeline_calls, store):
    """作业体字段必须原样交给 pipeline（位置参数顺序与签名一致）。"""
    payload = _job(
        new_task,
        video=transcribe_module.VideoInfo(**VIDEO),
        prefer_subtitles=False,
        hotwords=["转写"],
        engine="cloud",
    )
    response = client.post(WORKER_PATH, json=payload)
    assert response.status_code == 200

    args, kwargs = pipeline_calls[0]
    assert args[0] == new_task
    assert args[1] == URL
    assert args[2].videoId == "BV1GJ411x7h7"
    assert args[3] is False
    assert args[4] == ["转写"]
    assert args[5] == "cloud"
    assert callable(kwargs["on_heartbeat"])  # 锁续期钩子必须挂上


# ---- 幂等领取 ----

def test_worker_skips_when_lock_held(new_task, pipeline_calls, store):
    """另一实例已抢到锁：返回 200 skipped，且绝不执行 pipeline。"""
    token = asyncio.run(store.acquire(new_task, 60))
    assert token is not None

    response = client.post(WORKER_PATH, json=_job(new_task))

    assert response.status_code == 200
    assert response.json() == {"status": "skipped", "reason": "locked", "task_id": new_task}
    assert pipeline_calls == []
    assert asyncio.run(store.get(new_task)).status == "processing"


def test_worker_releases_lock_after_run(new_task, subtitle_ok, store):
    """执行完必须释放锁，否则平台重试会被永久挡住。"""
    client.post(WORKER_PATH, json=_job(new_task))
    assert asyncio.run(store.acquire(new_task, 60)) is not None


def test_worker_releases_lock_after_failure(new_task, monkeypatch, store):
    monkeypatch.setattr(
        transcribe_module, "run_transcription_pipeline", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    client.post(WORKER_PATH, json=_job(new_task))
    assert asyncio.run(store.acquire(new_task, 60)) is not None


def test_worker_skips_completed_task(new_task, pipeline_calls, store):
    """已完成的确定性结果不应被平台重试再跑一遍（云端 ASR 会重复计费）。"""
    asyncio.run(store.update(new_task, status="completed", progress=100))

    response = client.post(WORKER_PATH, json=_job(new_task))

    assert response.json() == {"status": "skipped", "reason": "already_completed", "task_id": new_task}
    assert pipeline_calls == []


def test_worker_skips_unknown_task(pipeline_calls):
    response = client.post(WORKER_PATH, json=_job("does-not-exist"))
    assert response.status_code == 200
    assert response.json() == {"status": "skipped", "reason": "task_not_found", "task_id": "does-not-exist"}
    assert pipeline_calls == []


# ---- 失败语义：业务失败 200 / 基础设施 5xx ----

def test_worker_returns_200_when_pipeline_marks_failed(new_task, monkeypatch, store):
    """pipeline 内部把异常收敛成 failed → Worker 返回 200（重试无意义）。"""
    monkeypatch.setattr(
        transcribe_module,
        "try_extract_subtitle_transcript",
        lambda url: ([{"time": 1.0, "text": "会被 _finish 打回"}], "subtitle_ai"),
    )
    monkeypatch.setattr(transcribe_module, "_finish", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("落库失败")))

    response = client.post(WORKER_PATH, json=_job(new_task))

    assert response.status_code == 200
    assert response.json()["task_status"] == "failed"
    task = asyncio.run(store.get(new_task))
    assert task.status == "failed"
    assert task.error["code"] == "TRANSCRIBE_FAILED"


def test_worker_returns_5xx_and_marks_failed_on_infra_error(new_task, monkeypatch, store):
    """胶水层/未捕获异常：标 failed 后回 5xx，让平台重试。"""
    monkeypatch.setattr(
        transcribe_module, "run_transcription_pipeline", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("thread died"))
    )

    response = client.post(WORKER_PATH, json=_job(new_task))

    assert response.status_code == 500
    task = asyncio.run(store.get(new_task))
    assert task.status == "failed"
    assert task.error == {"code": "TRANSCRIBE_FAILED", "message": "任务执行失败，请重试"}


def test_worker_does_not_overwrite_failed_task_on_infra_error(new_task, monkeypatch, store):
    """兜底只处理 processing：pipeline 已写好的错误信息不能被覆盖。"""
    original = {"code": "TRANSCRIBE_FAILED", "message": "原始错误"}

    def _fail_then_raise(*args, **kwargs):
        tasks_module.update_task(args[0], status="failed", error=original)
        raise RuntimeError("随后线程崩了")

    monkeypatch.setattr(transcribe_module, "run_transcription_pipeline", _fail_then_raise)

    response = client.post(WORKER_PATH, json=_job(new_task))

    assert response.status_code == 500
    assert asyncio.run(store.get(new_task)).error == original


# ---- 事件体宽容解包 ----

@pytest.mark.parametrize(
    "wrap",
    [
        pytest.param(lambda body: body, id="bare"),
        pytest.param(lambda body: {"body": json.dumps(body)}, id="body-json-string"),
        pytest.param(lambda body: {"body": body}, id="body-object"),
        pytest.param(lambda body: {"payload": body}, id="payload-object"),
        pytest.param(lambda body: {"payload": json.dumps(body)}, id="payload-json-string"),
        pytest.param(lambda body: {"event": {"data": body}}, id="nested-event-data"),
    ],
)
def test_worker_accepts_wrapped_events(new_task, subtitle_ok, store, wrap):
    response = client.post(WORKER_PATH, json=wrap(_job(new_task)))
    assert response.status_code == 200
    assert response.json()["task_status"] == "completed"


def test_worker_accepts_base64_body(new_task, subtitle_ok):
    import base64

    raw = base64.b64encode(json.dumps(_job(new_task)).encode()).decode()
    response = client.post(WORKER_PATH, json={"body": raw, "isBase64Encoded": True})
    assert response.status_code == 200
    assert response.json()["task_status"] == "completed"


@pytest.mark.parametrize(
    "payload",
    [{"url": URL}, {"task_id": "x"}, {"body": "not json"}, {"body": "%%%", "isBase64Encoded": True}, [1, 2]],
)
def test_worker_rejects_unparseable_event(payload):
    response = client.post(WORKER_PATH, json=payload)
    assert response.status_code == 422


# ---- 鉴权 ----

def test_worker_token_not_required_by_default(monkeypatch):
    monkeypatch.setattr("app.worker_app.WORKER_TOKEN", "")
    assert client.post(WORKER_PATH, json=_job("unknown")).status_code == 200


def test_worker_token_rejects_missing_and_wrong(monkeypatch):
    monkeypatch.setattr("app.worker_app.WORKER_TOKEN", "s3cret")
    assert client.post(WORKER_PATH, json=_job("unknown")).status_code == 401
    assert client.post(WORKER_PATH, json=_job("unknown"), headers={"X-Worker-Token": "nope"}).status_code == 401


def test_worker_token_accepts_matching_header(monkeypatch):
    monkeypatch.setattr("app.worker_app.WORKER_TOKEN", "s3cret")
    response = client.post(WORKER_PATH, json=_job("unknown"), headers={"X-Worker-Token": "s3cret"})
    assert response.status_code == 200


# ---- 初始化语义 ----

def test_worker_init_does_not_interrupt_other_processing_tasks(monkeypatch, tmp_path, subtitle_ok):
    """Worker 冷启动绝不能把别的实例在途任务标成 interrupted。"""
    fresh = tasks_module.MemoryTaskStore(str(tmp_path / "worker.db"))
    monkeypatch.setattr(tasks_module, "_STORE", fresh)
    monkeypatch.setattr("app.worker_app._initialized", False)

    from app.schemas import VideoInfo

    task_id = asyncio.run(fresh.create(VideoInfo(**VIDEO)))

    response = client.post(WORKER_PATH, json=_job(task_id))

    assert response.status_code == 200
    assert asyncio.run(fresh.get(task_id)).status == "completed"


def test_worker_init_keeps_unrelated_processing_task(monkeypatch, tmp_path):
    """Worker init 之后，无关的 processing 任务仍保持 processing。"""
    fresh = tasks_module.MemoryTaskStore(str(tmp_path / "worker2.db"))
    monkeypatch.setattr(tasks_module, "_STORE", fresh)
    monkeypatch.setattr("app.worker_app._initialized", False)

    from app.schemas import VideoInfo

    in_flight = asyncio.run(fresh.create(VideoInfo(**VIDEO)))

    client.post(WORKER_PATH, json=_job("unknown"))  # 触发懒初始化

    assert asyncio.run(fresh.get(in_flight)).status == "processing"


# ---- 其他 ----

def test_health_endpoint():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "role": "worker"}


def test_worker_app_has_no_rate_limit_middleware():
    """Worker 是平台内部入口：不能继承 API 的全局限流与 CORS。"""
    names = [m.cls.__name__ for m in worker_app.user_middleware]
    assert "RateLimitMiddleware" not in names
    assert "CORSMiddleware" not in names


# ---- InvokeFunction 兼容入口（S2-P3）----

def test_invoke_path_matches_fc_convention():
    """FC 的 InvokeFunction 固定把 event POST 到 /invoke，路径不能改。"""
    assert INVOKE_PATH == "/invoke"


def test_invoke_runs_job_from_raw_body(new_task, subtitle_ok, store):
    """octet-stream 原始字节也必须能解析（FC 的 event 就是裸 body）。"""
    raw = json.dumps(_job(new_task)).encode()

    response = client.post(INVOKE_PATH, content=raw, headers={"Content-Type": "application/octet-stream"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "task_id": new_task, "task_status": "completed"}
    assert asyncio.run(store.get(new_task)).plain_text == "Worker 跑通"


def test_invoke_shares_idempotency_with_main_path(new_task, pipeline_calls, store):
    """两个入口共用 _run_job：锁语义必须一致（不能各写一套）。"""
    token = asyncio.run(store.acquire(new_task, 60))
    assert token is not None

    response = client.post(INVOKE_PATH, content=json.dumps(_job(new_task)).encode())

    assert response.json() == {"status": "skipped", "reason": "locked", "task_id": new_task}
    assert pipeline_calls == []


def test_invoke_rejects_unparseable_body():
    response = client.post(INVOKE_PATH, content=b"not json")
    assert response.status_code == 422


def test_invoke_rejects_empty_body():
    response = client.post(INVOKE_PATH, content=b"")
    assert response.status_code == 422


def test_invoke_accepts_bearer_token(monkeypatch):
    """Authorization: Bearer 与 X-Worker-Token 等价（便于切换触发器认证方式）。"""
    monkeypatch.setattr("app.worker_app.WORKER_TOKEN", "s3cret")

    assert client.post(INVOKE_PATH, content=json.dumps(_job("unknown")).encode()).status_code == 401
    response = client.post(
        INVOKE_PATH,
        content=json.dumps(_job("unknown")).encode(),
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 200


def test_main_path_accepts_bearer_token(monkeypatch):
    monkeypatch.setattr("app.worker_app.WORKER_TOKEN", "s3cret")
    response = client.post(WORKER_PATH, json=_job("unknown"), headers={"Authorization": "Bearer s3cret"})
    assert response.status_code == 200


def test_bearer_token_must_match_exactly(monkeypatch):
    monkeypatch.setattr("app.worker_app.WORKER_TOKEN", "s3cret")
    for header in ("Bearer nope", "Basic s3cret", "s3cret", "Bearer "):
        assert client.post(WORKER_PATH, json=_job("unknown"), headers={"Authorization": header}).status_code == 401


def test_non_ascii_token_degrades_to_401_not_500(monkeypatch):
    """`secrets.compare_digest` 对含非 ASCII 的 str 会抛 TypeError → 500。

    HTTP header 本身只能传 ASCII，所以这种配置下**所有请求**都会失败；把比较改成
    bytes 后至少退化成干净的 401，而不是满屏 500。
    """
    monkeypatch.setattr("app.worker_app.WORKER_TOKEN", "口令-中文")

    for headers in ({"X-Worker-Token": "wrong"}, {"Authorization": "Bearer wrong"}, {}):
        assert client.post(WORKER_PATH, json=_job("unknown"), headers=headers).status_code == 401


# ---- FcDispatcher → worker_app 回环（S2-P3）----

def test_fc_dispatcher_delivers_body_worker_accepts(new_task, subtitle_ok):
    """FcDispatcher 发出的请求体，必须能被真实 worker 应用跑通。

    这覆盖 P3 最容易出错的两处拼接：路径（WORKER_PATH）与请求体格式。
    不联网、不需要 FC 环境，但走的是真实的 httpx 客户端代码路径。
    """
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(202, headers={"X-Fc-Request-Id": "local"})

    FcDispatcher(url="https://worker.test", transport=httpx.MockTransport(handler)).enqueue(
        TranscribeJob(task_id=new_task, url=URL)
    )

    assert len(captured) == 1
    request = captured[0]
    # 路径必须与 worker 实际挂载点一致
    assert request.url.path == WORKER_PATH

    # 同一份 body 喂给真实 worker 应用，确认能跑完
    response = client.post(
        WORKER_PATH,
        content=request.content,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["task_status"] == "completed"


# ---- 定时触发器分流 + Reaper 端点（S2-P4）----

def _timer_event(payload: str = '{"action":"reap"}') -> bytes:
    """FC 定时触发器事件体（官方格式，见 docs/serverless-deploy.md §5.4）。"""
    return json.dumps(
        {
            "triggerTime": "2023-12-26T07:49:00Z",
            "triggerName": "reapTimer",
            "payload": payload,
        }
    ).encode()


def _stale_task(store, seconds: float = 3600) -> str:
    """建一条心跳已停止的任务。"""
    import time

    from app.schemas import VideoInfo

    task_id = asyncio.run(store.create(VideoInfo(**VIDEO)))
    task = asyncio.run(store.get(task_id))
    task.updated_at = time.time() - seconds
    with store._lock:
        store._upsert_row(task)
    return task_id


@pytest.fixture()
def isolated_store(monkeypatch, tmp_path):
    """Reaper 会扫描**全库** processing 任务，必须用独立库，否则会收割别的用例留下的任务。"""
    fresh = tasks_module.MemoryTaskStore(str(tmp_path / "reaper.db"))
    monkeypatch.setattr(tasks_module, "_STORE", fresh)
    monkeypatch.setattr("app.worker_app._initialized", False)
    return fresh


def test_reap_path_is_not_the_invoke_path():
    """定时触发器打不到自定义路径，所以两个端点必须区分开（别把 REAP_PATH 配进 s.yaml）。"""
    assert REAP_PATH == "/internal/worker/reap"
    assert REAP_PATH != INVOKE_PATH


def test_reap_endpoint_reaps_stale_task(isolated_store):
    task_id = _stale_task(isolated_store)

    response = client.post(REAP_PATH)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["reaped"] == 1
    assert task_id in payload["task_ids"]
    assert asyncio.run(isolated_store.get(task_id)).status == "failed"
    assert asyncio.run(isolated_store.get(task_id)).error["code"] == "TASK_TIMEOUT"


def test_reap_endpoint_keeps_fresh_task(isolated_store):
    from app.schemas import VideoInfo

    task_id = asyncio.run(isolated_store.create(VideoInfo(**VIDEO)))

    response = client.post(REAP_PATH)

    assert response.status_code == 200
    assert response.json()["reaped"] == 0
    assert asyncio.run(isolated_store.get(task_id)).status == "processing"


def test_reap_endpoint_requires_token(monkeypatch, isolated_store):
    monkeypatch.setattr("app.worker_app.WORKER_TOKEN", "s3cret")

    assert client.post(REAP_PATH).status_code == 401
    assert client.post(REAP_PATH, headers={"X-Worker-Token": "nope"}).status_code == 401
    assert client.post(REAP_PATH, headers={"X-Worker-Token": "s3cret"}).status_code == 200


def test_invoke_routes_timer_event_to_reaper(isolated_store):
    """定时触发器事件必须走 reaper，而不是被当成作业体回 422。"""
    task_id = _stale_task(isolated_store)

    response = client.post(INVOKE_PATH, content=_timer_event())

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["reaped"] == 1
    assert asyncio.run(isolated_store.get(task_id)).status == "failed"


def test_invoke_timer_routing_ignores_payload_content(isolated_store):
    """payload 是用户自定义字符串，不参与路由（忘配 payload 不能让 reaper 静默失效）。"""
    task_id = _stale_task(isolated_store)

    response = client.post(INVOKE_PATH, content=_timer_event(payload="anything"))

    assert response.status_code == 200
    assert response.json()["reaped"] == 1
    assert asyncio.run(isolated_store.get(task_id)).status == "failed"


def test_invoke_timer_wins_over_task_id_in_payload(isolated_store):
    """判定顺序：Timer 优先。payload 里恰好有 task_id 也不能被当作业执行。"""
    payload = json.dumps({"triggerTime": "2023-12-26T07:49:00Z", "triggerName": "t", "payload": {"task_id": "x"}})
    task_id = _stale_task(isolated_store)

    response = client.post(INVOKE_PATH, content=payload.encode())

    assert response.status_code == 200
    assert "reaped" in response.json()  # 走的是 reaper 结果，不是作业结果
    assert asyncio.run(isolated_store.get(task_id)).status == "failed"


def test_invoke_still_runs_job_after_timer_routing(new_task, subtitle_ok):
    """分流不能破坏原有作业路径。"""
    response = client.post(INVOKE_PATH, content=json.dumps(_job(new_task)).encode())

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "task_id": new_task, "task_status": "completed"}


def test_invoke_rejects_body_without_timer_or_task_id():
    """两个特征都不满足仍是 422（不能被误判成定时事件）。"""
    response = client.post(INVOKE_PATH, content=json.dumps({"triggerTime": "2023-12-26T07:49:00Z"}).encode())
    assert response.status_code == 422


def test_timer_detection_handles_wrapped_body():
    """平台若把 event 塞进 body 包装层，也要能识别（与 _unwrap_event 同宽容度）。"""
    from app.worker_app import _looks_like_timer_event

    event = json.loads(_timer_event())
    assert _looks_like_timer_event(event) is True
    assert _looks_like_timer_event({"body": json.dumps(event)}) is True
    assert _looks_like_timer_event({"payload": event}) is True
    assert _looks_like_timer_event(_job("x")) is False
    assert _looks_like_timer_event(None) is False
    assert _looks_like_timer_event("not json") is False

