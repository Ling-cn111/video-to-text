"""重点帧抽帧（PR #89）：路由契约、Worker 分流、时间点净化、选流策略。

分工：本文件只测 frames 特性自身；worker_app 的通用转发/鉴权在
test_worker_app.py，终态例外的存储语义在 test_tasks_store.py。

所有用例不联网：httpx / pipeline / storage 均打桩；ffmpeg 真抽帧用例在无
ffmpeg 的环境自动跳过（线上镜像恒有；本地开发机按实际安装情况跳）。
"""
import asyncio
import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import VideoInfo
from app.services import bili_playurl
from app.services import frames as frames_module
from app.services import storage as storage_module
from app.services import tasks as tasks_module
from app.services.dispatcher import FramesJob
from app.worker_app import INVOKE_PATH, app as worker_app

client = TestClient(app)
worker_client = TestClient(worker_app)

TOKEN = "test-worker-token"

VIDEO = {
    "title": "重点帧测试视频",
    "cover": "https://i1.hdslb.com/bfs/archive/test.jpg",
    "duration": 754,
    "platform": "bilibili",
    "videoId": "BV1FramesTest",
}

URL = "https://www.bilibili.com/video/BV1FramesTest"


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path):
    """每个用例独立的任务库（内存 + 独立 SQLite），互不串扰。"""
    tasks_module.reset_task_store_for_tests(str(tmp_path / "tasks.db"))
    yield


@pytest.fixture(autouse=True)
def _worker_token(monkeypatch):
    """Worker 端点鉴权与 test_worker_app 同款：统一注入默认头。"""
    monkeypatch.setattr("app.worker_app.WORKER_TOKEN", TOKEN)
    worker_client.headers["X-Worker-Token"] = TOKEN


@pytest.fixture()
def oss_ready(monkeypatch):
    """OSS 配置探测打桩为 True（探测语义与 oss2 打桩手法见 test_upload.py）。"""
    monkeypatch.setattr(storage_module, "is_file_storage_configured", lambda: True)


@pytest.fixture()
def dispatched(monkeypatch):
    """捕获 enqueue_frames 的作业体（验证派发内容，不真跑 pipeline）。"""
    jobs: list[FramesJob] = []

    def _fake_enqueue(job, background_tasks=None):
        jobs.append(job)

    monkeypatch.setattr("app.routers.frames.enqueue_frames", _fake_enqueue)
    return jobs


def _make_task(
    source: str = "url",
    platform: str = "bilibili",
    video_id: str = "BV1FramesTest",
    completed: bool = True,
    frames: dict | None = None,
) -> str:
    store = tasks_module.get_task_store()
    video = VideoInfo(title="重点帧测试视频", cover="", duration=754, platform=platform, videoId=video_id)
    task_id = asyncio.run(store.create(video, "cloud", source=source))
    if completed:
        asyncio.run(store.update(task_id, status="completed", transcript=[], plain_text="x"))
    if frames is not None:
        asyncio.run(store.update(task_id, frames=frames))
    return task_id


# ---- 时间点净化（sanitize_timestamps） ----


def test_sanitize_clamps_merges_and_truncates():
    points = frames_module.sanitize_timestamps([999999, 0, 0.5, 92, 380, -5, "bad", float("nan")], 754, 12)
    assert points == [0, 92, 380, 753]  # 0/0.5/-5 合并；999999 钳到 duration-1；非法值丢弃


def test_sanitize_truncates_to_max_count():
    points = frames_module.sanitize_timestamps(list(range(0, 100)), None, 12)
    assert len(points) == 12
    # 间隔均 ≥ 2s（近邻合并生效）
    assert all(b - a >= frames_module.FRAME_MIN_GAP_SECONDS for a, b in zip(points, points[1:]))


def test_sanitize_never_returns_negative():
    assert frames_module.sanitize_timestamps([-100, -0.5], 0, 12) == [0]


# ---- 路由契约（POST /api/frames） ----


def test_router_requires_existing_task(oss_ready):
    response = client.post("/api/frames", json={"taskId": "missing", "timestamps": [1]})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INVALID_TASK"


def test_router_rejects_incomplete_task(oss_ready):
    task_id = _make_task(completed=False)
    response = client.post("/api/frames", json={"taskId": task_id, "timestamps": [1]})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "FRAMES_NOT_READY"


def test_router_rejects_file_task(oss_ready):
    task_id = _make_task(source="file", platform="local", video_id="uploads/abc")
    response = client.post("/api/frames", json={"taskId": task_id, "timestamps": [1]})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FRAMES_UNSUPPORTED"


def test_router_requires_oss():
    task_id = _make_task()  # storage 探测保持默认 False（本地未配 OSS）
    response = client.post("/api/frames", json={"taskId": task_id, "timestamps": [1]})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "FILE_STORAGE_NOT_CONFIGURED"


def test_router_dispatches_sanitized_job(oss_ready, dispatched):
    task_id = _make_task()
    response = client.post(
        "/api/frames",
        json={"taskId": task_id, "timestamps": [0, 0.5, 92, 380, 999999]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["frames"]["status"] == "processing"

    assert len(dispatched) == 1
    job = dispatched[0]
    # URL 由 videoId 重建 canonical 链接（不落任务库）
    assert job.url == URL
    # 时间点经服务端净化：0/0.5 合并、999999 钳到 duration-1
    assert job.timestamps == [0, 92, 380, 753]

    task = asyncio.run(tasks_module.get_task_store().get(task_id))
    assert task.frames["status"] == "processing"


def test_router_idempotent_while_processing(oss_ready, dispatched):
    task_id = _make_task(frames={"status": "processing", "error": None, "items": []})
    response = client.post("/api/frames", json={"taskId": task_id, "timestamps": [1]})
    assert response.status_code == 200
    assert response.json()["frames"]["status"] == "processing"
    assert dispatched == []  # 不重复派发


def test_router_idempotent_ready_returns_presigned_urls(oss_ready, dispatched, monkeypatch):
    task_id = _make_task()
    asyncio.run(
        tasks_module.get_task_store().update(
            task_id,
            frames={
                "status": "ready",
                "error": None,
                "items": [{"time": 12.5, "objectKey": f"frames/{task_id}/00.jpg"}],
            },
        )
    )
    # presign_get 打桩（签名是离线 HMAC，真值用例见 test_presign_get_offline_hmac）
    monkeypatch.setattr(
        storage_module,
        "presign_get",
        lambda key, expires: f"https://oss.example.com/{key}?Signature=stub&Expires={expires}",
    )
    response = client.post("/api/frames", json={"taskId": task_id, "timestamps": [1]})
    assert response.status_code == 200
    body = response.json()["frames"]
    assert body["status"] == "ready"
    assert body["items"][0]["time"] == 12.5
    assert body["items"][0]["url"].startswith("http")
    assert dispatched == []


def test_presign_get_offline_hmac(monkeypatch):
    """presign_get 是离线签名（不发网络请求）：配置齐备时本地就能签出可用的 URL。"""
    monkeypatch.setattr(storage_module, "OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setattr(storage_module, "OSS_BUCKET", "vtt-test-bucket")
    monkeypatch.setattr(storage_module, "OSS_ACCESS_KEY_ID", "test-ak")
    monkeypatch.setattr(storage_module, "OSS_ACCESS_KEY_SECRET", "test-sk")

    url = storage_module.presign_get(f"frames/{'a' * 32}/00.jpg", 3600)

    # oss2 会把 key URL 编码进路径（frames%2F...），OSS 侧等价接受
    assert url.startswith("https://vtt-test-bucket.oss-cn-hangzhou.aliyuncs.com/")
    assert "Signature=" in url
    assert "Expires=" in url


def test_router_dispatch_failure_marks_frames_failed(oss_ready, monkeypatch):
    task_id = _make_task()

    def _boom(job, background_tasks=None):
        raise RuntimeError("fc down")

    monkeypatch.setattr("app.routers.frames.enqueue_frames", _boom)
    # raise_server_exceptions=False：模拟真实 HTTP 行为（异常由 main.py 的兜底 handler 转 500）
    tolerant_client = TestClient(app, raise_server_exceptions=False)
    response = tolerant_client.post("/api/frames", json={"taskId": task_id, "timestamps": [1]})
    # 分发失败向上抛 → 全局兜底 500；但 frames 状态已落库（前端轮询可见失败）
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    task = asyncio.run(tasks_module.get_task_store().get(task_id))
    assert task.frames["status"] == "failed"
    assert task.frames["error"]["code"] == "FRAMES_FAILED"


# ---- Worker 分流与执行语义 ----


def _frames_job(task_id: str, **overrides) -> dict:
    body = {"kind": "frames", "task_id": task_id, "url": URL, "timestamps": [1.0, 92.0]}
    body.update(overrides)
    return body


def test_worker_frames_job_completes(monkeypatch):
    task_id = _make_task()

    def _fake_pipeline(task_id_, url, timestamps):
        tasks_module.update_task(
            task_id_,
            frames={
                "status": "ready",
                "error": None,
                "items": [{"time": 1.0, "objectKey": f"frames/{task_id_}/00.jpg"}],
            },
        )

    monkeypatch.setattr(frames_module, "run_frames_pipeline", _fake_pipeline)
    response = worker_client.post(INVOKE_PATH, json=_frames_job(task_id))
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "task_id": task_id, "frames_status": "ready"}


def test_worker_frames_job_skipped_when_ready():
    task_id = _make_task(frames={"status": "ready", "error": None, "items": []})
    response = worker_client.post(INVOKE_PATH, json=_frames_job(task_id))
    assert response.status_code == 200
    assert response.json()["status"] == "skipped"
    assert response.json()["reason"] == "frames_ready"


def test_worker_frames_job_rejects_private_url():
    task_id = _make_task()
    response = worker_client.post(
        INVOKE_PATH, json=_frames_job(task_id, url="http://127.0.0.1:8080/video/BV1x")
    )
    assert response.status_code == 200  # 确定性失败：200 skipped，不触发平台重试
    assert response.json()["status"] == "skipped"
    assert response.json()["reason"] == "invalid_url"
    task = asyncio.run(tasks_module.get_task_store().get(task_id))
    assert task.frames["status"] == "failed"


def test_worker_frames_job_requires_kind_marker():
    task_id = _make_task()
    # kind=frames 但缺 timestamps：FramesJob.from_dict 拒绝 → 422
    response = worker_client.post(
        INVOKE_PATH, json={"kind": "frames", "task_id": task_id, "url": URL}
    )
    assert response.status_code == 422


def test_worker_frames_dedicated_route(monkeypatch):
    """fc 模式投递的专用路由（FRAMES_WORKER_PATH）与 /invoke 分流同语义。"""
    from app.config import FRAMES_WORKER_PATH

    task_id = _make_task()
    monkeypatch.setattr(
        frames_module,
        "run_frames_pipeline",
        lambda *args, **kwargs: tasks_module.update_task(
            task_id, frames={"status": "ready", "error": None, "items": []}
        ),
    )
    response = worker_client.post(FRAMES_WORKER_PATH, json=_frames_job(task_id))
    assert response.status_code == 200
    assert response.json()["frames_status"] == "ready"


def test_worker_transcribe_job_ignores_frames_body_without_kind():
    """无 kind 标记的作业体按转写作业解析：缺 url 字段 → 422（原有校验不回退）。"""
    task_id = _make_task()
    response = worker_client.post(
        INVOKE_PATH, json={"task_id": task_id, "timestamps": [1]}
    )
    assert response.status_code == 422


# ---- 选流策略（get_video_stream） ----


def _playurl_payload(videos: list[dict]) -> dict:
    return {
        "code": 0,
        "data": {
            "dash": {
                "video": videos,
                "audio": [{"id": 30280, "baseUrl": "https://cdn/audio", "bandwidth": 200000}],
            }
        },
    }


def _client_returning(monkeypatch, payload_by_url: dict, seen_urls: list[str]):
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

        def __init__(self, payload):
            self._payload = payload

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, **kwargs):
            seen_urls.append(url)
            for key, payload in payload_by_url.items():
                if key in url:
                    return _Resp(payload)
            raise AssertionError(f"未预置的 URL：{url}")

    monkeypatch.setattr(bili_playurl.httpx, "Client", _Client)


def test_video_stream_prefers_highest_tier_then_hevc(monkeypatch):
    videos = [
        {"id": 32, "width": 852, "height": 480, "codecs": "avc1.64001F", "bandwidth": 786766, "baseUrl": "https://cdn/480-avc", "backupUrl": []},
        {"id": 80, "width": 1920, "height": 1080, "codecs": "avc1.640032", "bandwidth": 2629444, "baseUrl": "https://cdn/1080-avc", "backupUrl": ["https://cdn/1080-avc-bak"]},
        {"id": 80, "width": 1920, "height": 1080, "codecs": "hev1.1.6.L150.90", "bandwidth": 749418, "baseUrl": "https://cdn/1080-hev", "backupUrl": []},
        {"id": 120, "width": 3840, "height": 2160, "codecs": "avc1.640033", "bandwidth": 8000000, "baseUrl": "https://cdn/4k", "backupUrl": []},
    ]
    seen: list[str] = []
    _client_returning(monkeypatch, {"playurl": _playurl_payload(videos)}, seen)

    stream = bili_playurl.get_video_stream("BV1FramesTest", cid=1, max_height=1080)
    # 4K（高度超上限）被过滤；qn=80 为剩余最高档；同档优先 hev1（体积小）
    assert stream.quality == 80
    assert stream.codecs.startswith("hev1")
    assert stream.backup_urls == ()
    # 关键契约：请求必须带 try_look=1（未登录解锁 1080P 的唯一实测手段）
    assert any("try_look=1" in url for url in seen)


def test_video_stream_falls_back_to_avc_when_no_hevc(monkeypatch):
    videos = [
        {"id": 80, "width": 1920, "height": 1080, "codecs": "avc1.640032", "bandwidth": 2629444, "baseUrl": "https://cdn/1080-avc", "backupUrl": ["https://cdn/bak"]},
    ]
    _client_returning(monkeypatch, {"playurl": _playurl_payload(videos)}, [])

    stream = bili_playurl.get_video_stream("BV1FramesTest", cid=1, max_height=1080)
    assert stream.quality == 80
    assert stream.codecs.startswith("avc1")
    assert stream.backup_urls == ("https://cdn/bak",)


def test_video_stream_raises_without_video_track(monkeypatch):
    _client_returning(monkeypatch, {"playurl": _playurl_payload([])}, [])
    with pytest.raises(ValueError, match="未返回 DASH 视频轨"):
        bili_playurl.get_video_stream("BV1FramesTest", cid=1)


# ---- 契约序列化（frames_to_out） ----


def test_frames_to_out_failed_shape():
    out = frames_module.frames_to_out(
        {"status": "failed", "error": {"code": "FRAMES_FAILED", "message": "下载失败"}, "items": []}
    )
    assert out.status == "failed"
    assert out.error is not None
    assert out.error.message == "下载失败"


def test_frames_to_out_skips_invalid_items_and_unsigned_urls():
    key = f"frames/{'a' * 32}/00.jpg"
    out = frames_module.frames_to_out(
        {"status": "ready", "items": [{"time": 1.5, "objectKey": key}, {"objectKey": ""}]}
    )
    assert len(out.items) == 1
    assert out.items[0].url == ""  # 未要求 presign 时不签发
    assert out.items[0].time == 1.5


# ---- 真实 ffmpeg 抽帧（无 ffmpeg 环境自动跳过） ----


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="本机无 ffmpeg")
def test_extract_frame_real_ffmpeg(tmp_path):
    """真 ffmpeg 端到端：lavfi 合成 2s 测试视频 → 抽 1 帧 → JPEG 非空。

    这条用例守住 _extract_frame 的命令行参数（输入寻址 / 缩放 / q:v），
    参数写错时本地必红，不必等部署到云端才发现。
    """
    source = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
            "-pix_fmt", "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    target = tmp_path / "f.jpg"
    frames_module._extract_frame(str(source), 1.0, str(target))
    assert target.exists()
    assert target.stat().st_size > 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="本机无 ffmpeg")
def test_extract_frame_beyond_duration_raises(tmp_path):
    """越界时间点：产物为空/失败 → 明确抛 ValueError（管线据此跳过该点）。"""
    source = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
            "-pix_fmt", "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    target = tmp_path / "f.jpg"
    with pytest.raises(ValueError):
        frames_module._extract_frame(str(source), 30.0, str(target))
