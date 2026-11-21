"""file 来源任务测试（M3）：路由分流 + Worker 校验分支 + 管线取音。

三层各一节：
- 路由（POST /api/transcribe kind=file）：对象存在性/大小复核 → 派发载荷
- Worker（_run_job）：file 作业的对象键白名单兜底（不信任派发方）
- 管线（run_transcription_pipeline source=file）：OSS 落盘 → 转码 → 时长回填 → 成功删对象
"""
import io
import contextlib
import re
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import FileRef, VideoInfo
from app.services import dispatcher as dispatcher_module
from app.services import storage
from app.services import tasks as tasks_module
from app.services import transcribe as transcribe_module
from app.worker_app import app as worker_app

client = TestClient(app)
worker_client = TestClient(worker_app)

OBJECT_KEY = "uploads/00000000000000000000000000000000/class_recording.mp4"
FILE_BODY = {
    "kind": "file",
    "file": {"objectKey": OBJECT_KEY, "fileName": "课堂录像.mp4", "size": 123},
}


@pytest.fixture(autouse=True)
def _oss_ready(monkeypatch):
    """OSS 已配置 + 对象已就位（HEAD 返回 123 字节）。"""
    monkeypatch.setattr(storage, "is_file_storage_configured", lambda: True)
    monkeypatch.setattr(storage, "head_size", lambda key: 123 if key == OBJECT_KEY else None)


@pytest.fixture(autouse=True)
def _local_engine(monkeypatch):
    """固定走 local 引擎：与 url 任务的分发测试同款，避免云端 Key 预检干扰。"""
    monkeypatch.setattr("app.routers.transcribe.CLOUD_ASR_API_KEY", "")


@pytest.fixture()
def captured_job(monkeypatch):
    captured = []

    class _SpyDispatcher:
        mode = "inline"

        def enqueue(self, job, background_tasks=None):
            captured.append(job)

    monkeypatch.setattr(dispatcher_module, "_DISPATCHER", _SpyDispatcher())
    return captured


# ---- 路由层：分流与复核 ----

def test_file_task_dispatches_file_job(captured_job):
    response = client.post("/api/transcribe", json=FILE_BODY)
    assert response.status_code == 200
    body = response.json()
    assert body["stage"] == "extract_audio"  # 文件任务没有「解析链接」阶段

    job = captured_job[0]
    assert job.task_id == body["taskId"]
    assert job.source == "file"
    assert job.url is None
    assert job.file is not None and job.file.objectKey == OBJECT_KEY
    assert job.prefer_subtitles is False  # 本地文件没有视频页 URL，字幕快路径不适用
    # 合成 VideoInfo：标题=文件名、无封面、platform=local、videoId=objectKey
    assert job.video is not None
    assert job.video.title == "课堂录像.mp4"
    assert job.video.cover == ""
    assert job.video.platform == "local"
    assert job.video.videoId == OBJECT_KEY
    assert job.video.duration == 0  # 真实时长由 Worker 回填

    task = tasks_module.get_task(body["taskId"])
    assert task.source == "file"
    assert task.file == {"objectKey": OBJECT_KEY, "fileName": "课堂录像.mp4", "size": 123}


def test_file_task_accepts_client_video_override(captured_job):
    video = VideoInfo(title="自定义标题", cover="", duration=0, platform="local", videoId=OBJECT_KEY)
    response = client.post("/api/transcribe", json={**FILE_BODY, "video": video.model_dump()})
    assert response.status_code == 200
    assert captured_job[0].video.title == "自定义标题"


def test_file_task_missing_object_is_file_not_found(monkeypatch):
    monkeypatch.setattr(storage, "head_size", lambda key: None)
    response = client.post("/api/transcribe", json=FILE_BODY)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FILE_NOT_FOUND"


def test_file_task_oversize_object_rejected(monkeypatch):
    monkeypatch.setattr(storage, "head_size", lambda key: 600 * 1024 * 1024)
    response = client.post("/api/transcribe", json=FILE_BODY)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_file_task_forged_object_key_rejected():
    """伪造 objectKey（白名单外）：与「文件未就绪」同回应——重新上传。"""
    forged = {**FILE_BODY, "file": {"objectKey": "secret/../../x.mp4", "fileName": "x.mp4", "size": 1}}
    response = client.post("/api/transcribe", json=forged)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FILE_NOT_FOUND"


def test_file_task_without_file_ref_is_422():
    response = client.post("/api/transcribe", json={"kind": "file"})
    assert response.status_code == 422  # pydantic model_validator 拦截


def test_url_task_still_requires_videoid():
    """旧契约保持：kind=url 缺 videoId → 422（M3 改动不得放宽 URL 流校验）。"""
    response = client.post("/api/transcribe", json={"url": "https://www.bilibili.com/video/BV1GJ411x7h7"})
    assert response.status_code == 422


def test_file_task_without_oss_configured(monkeypatch):
    monkeypatch.setattr(storage, "is_file_storage_configured", lambda: False)
    response = client.post("/api/transcribe", json=FILE_BODY)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "FILE_STORAGE_NOT_CONFIGURED"


# ---- Worker 层：对象键白名单兜底 ----

def _worker_headers():
    # 与各用例 monkeypatch 的 WORKER_TOKEN="tok" 对齐（config 默认为空，fail-closed）
    return {"X-Worker-Token": "tok"}


def test_worker_skips_forged_file_key(monkeypatch):
    monkeypatch.setattr("app.worker_app.WORKER_TOKEN", "tok")
    job = {
        "task_id": "t-forged",
        "source": "file",
        "file": {"objectKey": "evil/../../x.bin", "fileName": "x.bin", "size": 1},
        "prefer_subtitles": False,
    }
    # 任务不存在 → 无论如何都是 skipped（不触碰存储）
    response = worker_client.post(
        "/internal/worker/transcribe", json=job, headers=_worker_headers()
    )
    assert response.status_code == 200
    assert response.json()["reason"] == "invalid_file_ref"


def test_worker_skips_file_job_missing_ref(monkeypatch):
    monkeypatch.setattr("app.worker_app.WORKER_TOKEN", "tok")
    response = worker_client.post(
        "/internal/worker/transcribe",
        json={"task_id": "t-noref", "source": "file", "prefer_subtitles": False},
        headers=_worker_headers(),
    )
    assert response.status_code == 200
    assert response.json()["reason"] == "invalid_file_ref"


def test_worker_runs_file_pipeline_with_source_kwargs(monkeypatch):
    """合法 file 作业：pipeline 以 source/file 关键字参数被调用（不真跑下载）。"""
    monkeypatch.setattr("app.worker_app.WORKER_TOKEN", "tok")
    seen = {}

    def _fake_pipeline(task_id, url, video=None, prefer_subtitles=True, hotwords=None,
                       engine=None, on_heartbeat=None, byok_key=None, source="url", file=None):
        seen["args"] = (task_id, url)
        seen["source"] = source
        seen["file"] = file

    monkeypatch.setattr(transcribe_module, "run_transcription_pipeline", _fake_pipeline)

    # 准备一个真实任务（worker 会查存储并领锁）
    task_id = tasks_module.create_task(
        VideoInfo(title="x", cover="", duration=0, platform="local", videoId=OBJECT_KEY),
        engine="local",
        source="file",
        file={"objectKey": OBJECT_KEY, "fileName": "x.mp4", "size": 1},
    )
    response = worker_client.post(
        "/internal/worker/transcribe",
        json={
            "task_id": task_id,
            "source": "file",
            "url": None,
            "file": {"objectKey": OBJECT_KEY, "fileName": "x.mp4", "size": 1},
            "prefer_subtitles": False,
            "engine": "local",
        },
        headers=_worker_headers(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert seen["args"] == (task_id, None)
    assert seen["source"] == "file"
    assert seen["file"].objectKey == OBJECT_KEY


# ---- 管线层：OSS 落盘 → 转码 → 时长回填 → 成功删对象 ----

def _make_wav_bytes(seconds: int) -> bytes:
    """0 填充的「伪 WAV」：转码被桩替换，字节数只用于时长回填断言（32000 字节/秒）。"""
    return b"\x00" * (32000 * seconds)


def test_pipeline_file_task_completes_and_backfills_duration(monkeypatch):
    task_id = tasks_module.create_task(
        VideoInfo(title="课堂录像.mp4", cover="", duration=0, platform="local", videoId=OBJECT_KEY),
        engine="local",
        source="file",
        file={"objectKey": OBJECT_KEY, "fileName": "课堂录像.mp4", "size": 1},
    )

    @contextlib.contextmanager
    def _fake_download(object_key, max_bytes):
        assert object_key == OBJECT_KEY
        yield "/tmp/fake-source.mp4"

    deleted = []
    monkeypatch.setattr(storage, "download_to_temp", _fake_download)
    monkeypatch.setattr(
        transcribe_module, "convert_file_to_wav_16k_mono",
        lambda path: io.BytesIO(_make_wav_bytes(65)),
    )
    monkeypatch.setattr(storage, "delete_object", lambda key: deleted.append(key))
    monkeypatch.setattr(
        transcribe_module.asr, "transcribe_audio",
        lambda audio, **kwargs: [{"time": 0.0, "text": "大家好"}, {"time": 3.0, "text": "开始上课"}],
    )
    monkeypatch.setattr("app.services.separation.is_demucs_available", lambda: False)

    transcribe_module.run_transcription_pipeline(
        task_id, None,
        video=VideoInfo(title="课堂录像.mp4", cover="", duration=0, platform="local", videoId=OBJECT_KEY),
        source="file",
        file=FileRef(objectKey=OBJECT_KEY, fileName="课堂录像.mp4", size=1),
    )

    task = tasks_module.get_task(task_id)
    assert task.status == "completed"
    assert task.transcript_source == "asr"
    # 时长回填：64000*65? -> 32000 字节/秒 × 65 秒 = 65
    assert task.video.duration == 65
    assert [item.text for item in task.transcript] == ["大家好", "开始上课"]
    assert deleted == [OBJECT_KEY]  # 成功后对象被清理


def test_pipeline_file_task_failure_keeps_object_for_retry(monkeypatch):
    task_id = tasks_module.create_task(
        VideoInfo(title="x", cover="", duration=0, platform="local", videoId=OBJECT_KEY),
        engine="local",
        source="file",
        file={"objectKey": OBJECT_KEY, "fileName": "x.mp4", "size": 1},
    )

    @contextlib.contextmanager
    def _failing_download(object_key, max_bytes):
        raise storage.FileTooLargeError()
        yield ""  # pragma: no cover

    deleted = []
    monkeypatch.setattr(storage, "download_to_temp", _failing_download)
    monkeypatch.setattr(storage, "delete_object", lambda key: deleted.append(key))

    transcribe_module.run_transcription_pipeline(
        task_id, None, source="file",
        file=FileRef(objectKey=OBJECT_KEY, fileName="x.mp4", size=1),
    )

    task = tasks_module.get_task(task_id)
    assert task.status == "failed"
    assert task.error["code"] == "TRANSCRIBE_FAILED"
    assert deleted == []  # 失败保留对象（≤1 天生命周期兜底），重试免重传
