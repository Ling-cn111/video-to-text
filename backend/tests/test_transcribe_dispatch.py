"""分发层与路由的集成（S2-P1）：POST /api/transcribe 的实际分发行为。

与 test_dispatcher.py 的分工：那里验单元行为（作业体、各模式、参数透传），
这里验「HTTP 请求 → 分发 → 任务状态」的端到端衔接，包括分发失败的兜底。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import dispatcher as dispatcher_module
from app.services import tasks as tasks_module
from app.services.dispatcher import _build_dispatcher

client = TestClient(app)

VIDEO_META = {
    "title": "测试视频",
    "cover": "https://i1.hdslb.com/bfs/archive/test.jpg",
    "duration": 135,
    "platform": "bilibili",
    "videoId": "BV1GJ411x7h7",
}

BODY = {"videoId": "BV1GJ411x7h7", "video": VIDEO_META, "engine": "local"}


@pytest.fixture(autouse=True)
def _local_engine(monkeypatch):
    """固定走 local：避免云端 Key 预检干扰分发断言。"""
    monkeypatch.setattr("app.routers.transcribe.ASR_ENGINE", "local")
    monkeypatch.setattr("app.routers.transcribe.CLOUD_ASR_API_KEY", "")


@pytest.fixture()
def captured_job(monkeypatch):
    """拦截 inline 分发，捕获作业体（不执行 pipeline）。"""
    captured = []

    class _SpyDispatcher:
        mode = "inline"

        def enqueue(self, job, background_tasks=None):
            captured.append(job)

    monkeypatch.setattr(dispatcher_module, "_DISPATCHER", _SpyDispatcher())
    return captured


def test_post_dispatches_full_job(captured_job):
    """路由必须把完整作业交给 dispatcher：url / video / hotwords / engine 一个都不能丢。"""
    response = client.post(
        "/api/transcribe",
        json={**BODY, "hotwords": ["转写"], "url": "https://www.bilibili.com/video/BV1GJ411x7h7"},
    )
    assert response.status_code == 200

    assert len(captured_job) == 1
    job = captured_job[0]
    assert job.task_id == response.json()["taskId"]
    assert job.url == "https://www.bilibili.com/video/BV1GJ411x7h7"
    assert job.video is not None and job.video.videoId == "BV1GJ411x7h7"
    assert job.prefer_subtitles is True
    assert job.hotwords == ["转写"]
    assert job.engine == "local"


def test_post_rebuilds_url_from_video_id(captured_job):
    """url 缺省时按平台模板重建，并原样交给分发层。"""
    response = client.post("/api/transcribe", json=BODY)
    assert response.status_code == 200
    assert captured_job[0].url == "https://www.bilibili.com/video/BV1GJ411x7h7"


def test_post_marks_task_failed_when_dispatch_raises(monkeypatch):
    """分发失败（如 ENQUEUE_MODE=mns 未实现）→ 任务落 failed，不留孤儿 processing。"""
    monkeypatch.setattr(dispatcher_module, "_DISPATCHER", _build_dispatcher("mns"))
    # raise_server_exceptions=False：模拟真实 HTTP 行为（异常由 main.py 的兜底 handler 转 500）
    tolerant_client = TestClient(app, raise_server_exceptions=False)
    store = tasks_module._STORE
    before = set(store._tasks)

    response = tolerant_client.post("/api/transcribe", json=BODY)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"

    # 任务已被创建但必须标记 failed（否则前端会无限轮询 processing）
    new_ids = set(store._tasks) - before
    assert len(new_ids) == 1
    task = store._tasks[new_ids.pop()]
    assert task.status == "failed"
    assert task.error == {"code": "TRANSCRIBE_FAILED", "message": "任务分发失败，请重试"}
    assert task.video is not None and task.video.videoId == "BV1GJ411x7h7"


def test_post_inline_mode_still_completes_subtitle_flow(monkeypatch):
    """默认 inline 模式下，HTTP 全链路与改造前一致（响应后执行 pipeline）。"""
    monkeypatch.setattr(dispatcher_module, "_DISPATCHER", _build_dispatcher("inline"))
    monkeypatch.setattr(
        "app.services.transcribe.try_extract_subtitle_transcript",
        lambda url: ([{"time": 1.0, "text": "分发后仍能完成"}], "subtitle_ai"),
    )

    created = client.post("/api/transcribe", json=BODY)
    assert created.status_code == 200

    detail = client.get(f"/api/transcribe/{created.json()['taskId']}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"
    assert detail.json()["plainText"] == "分发后仍能完成"


# ---- ENQUEUE_MODE=fc：投递到 FC 异步（S2-P3）----

WORKER_URL = "https://video-to-text-worker.abc123.cn-hangzhou.fcapp.run"


def _fc_dispatcher(seen: list, status_code: int = 202):
    """构造打到 MockTransport 的 FcDispatcher（不联网）。"""
    import httpx

    from app.services.dispatcher import FcDispatcher

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status_code, headers={"X-Fc-Request-Id": "req-1"})

    return FcDispatcher(url=WORKER_URL, token="s3cret", transport=httpx.MockTransport(handler))


def test_post_fc_mode_dispatches_without_running_pipeline(monkeypatch):
    """fc 模式：请求内只投递，不同步执行 pipeline，任务保持 processing 供轮询。"""
    seen: list = []
    monkeypatch.setattr(dispatcher_module, "_DISPATCHER", _fc_dispatcher(seen))

    pipeline_calls = []
    monkeypatch.setattr(
        "app.services.transcribe.run_transcription_pipeline",
        lambda *a, **k: pipeline_calls.append(a),
    )

    response = client.post("/api/transcribe", json=BODY)

    assert response.status_code == 200
    assert response.json()["status"] == "processing"
    assert len(seen) == 1
    assert seen[0].headers["X-Fc-Invocation-Type"] == "Async"
    assert seen[0].headers["X-Worker-Token"] == "s3cret"
    assert pipeline_calls == []

    # 任务仍是 processing（真正的完成由 worker 写回）
    detail = client.get(f"/api/transcribe/{response.json()['taskId']}")
    assert detail.json()["status"] == "processing"


def test_post_fc_mode_sends_complete_job(monkeypatch):
    """投递的 body 必须含 pipeline 需要的全部字段（worker 侧不再回读 Redis 补字段）。"""
    import json

    seen: list = []
    monkeypatch.setattr(dispatcher_module, "_DISPATCHER", _fc_dispatcher(seen))

    response = client.post(
        "/api/transcribe",
        json={**BODY, "hotwords": ["转写"], "url": "https://www.bilibili.com/video/BV1GJ411x7h7"},
    )

    body = json.loads(seen[0].content)
    assert body["task_id"] == response.json()["taskId"]
    assert body["url"] == "https://www.bilibili.com/video/BV1GJ411x7h7"
    assert body["video"]["videoId"] == "BV1GJ411x7h7"
    assert body["hotwords"] == ["转写"]
    assert body["prefer_subtitles"] is True
    assert body["engine"] == "local"


def test_post_fc_mode_marks_failed_when_fc_rejects(monkeypatch):
    """FC 拒收（如 404 路径配错）→ 任务落 failed，不让前端无限轮询。"""
    monkeypatch.setattr(dispatcher_module, "_DISPATCHER", _fc_dispatcher([], status_code=404))
    tolerant_client = TestClient(app, raise_server_exceptions=False)
    store = tasks_module._STORE
    before = set(store._tasks)

    response = tolerant_client.post("/api/transcribe", json=BODY)

    assert response.status_code == 500
    new_ids = set(store._tasks) - before
    assert len(new_ids) == 1
    task = store._tasks[new_ids.pop()]
    assert task.status == "failed"
    assert task.error == {"code": "TRANSCRIBE_FAILED", "message": "任务分发失败，请重试"}


def test_post_fc_mode_marks_failed_when_url_missing(monkeypatch):
    """WORKER_URL 漏配：启动不崩，但请求必须明确失败（不静默丢任务）。"""
    import httpx

    from app.services.dispatcher import FcDispatcher

    monkeypatch.setattr(
        dispatcher_module,
        "_DISPATCHER",
        FcDispatcher(url="", transport=httpx.MockTransport(lambda r: httpx.Response(202))),
    )
    tolerant_client = TestClient(app, raise_server_exceptions=False)
    store = tasks_module._STORE
    before = set(store._tasks)

    response = tolerant_client.post("/api/transcribe", json=BODY)

    assert response.status_code == 500
    new_ids = set(store._tasks) - before
    assert len(new_ids) == 1
    task_id = new_ids.pop()
    assert store._tasks[task_id].status == "failed"
    assert tolerant_client.get(f"/api/transcribe/{task_id}").json()["status"] == "failed"

