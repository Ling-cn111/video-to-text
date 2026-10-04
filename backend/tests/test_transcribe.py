"""转写接口与流水线测试。

- 无标记：字幕解析 / 任务状态机 / 校验（不访问外网）
- @pytest.mark.network：真实下载音频 + faster-whisper 推理（CI 跳过，本地全量跑）
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.tasks import create_task, get_task
from app.services.transcribe import run_transcription_pipeline
from app.services.subtitles import parse_subtitle_body

client = TestClient(app)

# 无字幕、可直接提取的 135 秒科普视频（真实 ASR 路径测试用）
REAL_NO_SUBTITLE_URL = "https://www.bilibili.com/video/av116187438517161"

VIDEO_META = {
    "title": "测试视频",
    "cover": "https://i1.hdslb.com/bfs/archive/test.jpg",
    "duration": 135,
    "platform": "bilibili",
    "videoId": "BV1GJ411x7h7",
}


# ---- 字幕解析（真实格式 fixture）----

BILIBILI_CC_JSON = """
{"body":[{"from":0.48,"to":2.10,"content":"大家好，欢迎来到频道。"},{"from":2.10,"to":4.00,"content":"今天聊聊语音转写。"}]}
"""

JSON3_TEXT = """
{"events":[{"tStartMs":500,"dDurationMs":1500,"segs":[{"utf8":"第一句"},{"utf8":"字幕"}]},
{"tStartMs":2500,"dDurationMs":1200,"segs":[{"utf8":"第二句字幕"}]}]}
"""

VTT_TEXT = """
WEBVTT

00:00:01.000 --> 00:00:02.500
VTT 第一句

00:00:03.000 --> 00:00:04.200
VTT 第二句
"""


def test_parse_bilibili_cc_json():
    items = parse_subtitle_body(BILIBILI_CC_JSON, "json")
    assert items == [
        {"time": 0.48, "text": "大家好，欢迎来到频道。"},
        {"time": 2.1, "text": "今天聊聊语音转写。"},
    ]


def test_parse_json3():
    items = parse_subtitle_body(JSON3_TEXT, "json3")
    assert items[0] == {"time": 0.5, "text": "第一句字幕"}
    assert items[1] == {"time": 2.5, "text": "第二句字幕"}


def test_parse_vtt():
    items = parse_subtitle_body(VTT_TEXT, "vtt")
    assert items[0]["time"] == 1.0
    assert "VTT 第一句" in items[0]["text"]
    assert len(items) == 2


def test_parse_unparseable_returns_none():
    assert parse_subtitle_body("<html>not a subtitle</html>", "json") is None


# ---- 接口校验与状态机（不访问外网）----

def test_start_transcribe_requires_videoid():
    response = client.post("/api/transcribe", json={"url": "https://www.bilibili.com/video/BV1GJ411x7h7"})
    assert response.status_code == 422  # videoId 为必填字段，pydantic 层拦截


def test_start_transcribe_rejects_private_url():
    response = client.post(
        "/api/transcribe",
        json={"videoId": "BV1GJ411x7h7", "url": "http://127.0.0.1/video/x"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_URL"


def test_start_transcribe_rejects_unsupported_platform():
    response = client.post(
        "/api/transcribe",
        json={"videoId": "abc123", "url": "https://www.youtube.com/watch?v=x"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSUPPORTED_PLATFORM"


def test_get_unknown_task_returns_404():
    response = client.get("/api/transcribe/not-a-real-task-id")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INVALID_TASK"


def test_pipeline_subtitle_fast_path(monkeypatch):
    """字幕快路径：注入字幕轨道后任务秒级完成（不下载音频）。"""
    monkeypatch.setattr(
        "app.services.transcribe.try_extract_subtitle_transcript",
        lambda url: [{"time": 0.5, "text": "你好"}, {"time": 2.0, "text": "世界"}],
    )

    task_id = create_task(None)
    run_transcription_pipeline(task_id, "https://www.bilibili.com/video/BV1GJ411x7h7", None, prefer_subtitles=True)

    task = get_task(task_id)
    assert task is not None and task.status == "completed"
    assert task.progress == 100
    assert [(i.time, i.text) for i in task.transcript] == [(0.5, "你好"), (2.0, "世界")]
    assert task.plain_text == "你好世界"


def test_pipeline_failure_sets_friendly_error(monkeypatch):
    """失败路径：任务标记 failed，错误信息为友好中文。"""
    from app.errors import ParseFailedError

    monkeypatch.setattr(
        "app.services.transcribe.try_extract_subtitle_transcript",
        lambda url: None,
    )
    monkeypatch.setattr(
        "app.services.transcribe.download_audio",
        lambda url, dest: (_ for _ in ()).throw(ParseFailedError("音频下载失败：视频不存在")),
    )

    task_id = create_task(None)
    run_transcription_pipeline(task_id, "https://www.bilibili.com/video/BV1GJ411x7h7", None, prefer_subtitles=False)

    task = get_task(task_id)
    assert task.status == "failed"
    assert task.error == {"code": "TRANSCRIBE_FAILED", "message": "音频下载失败：视频不存在"}


def test_http_subtitle_flow(monkeypatch):
    """HTTP 层：POST 创建任务 → BackgroundTasks 执行字幕快路径 → GET 拿到文字稿。"""
    monkeypatch.setattr(
        "app.services.transcribe.try_extract_subtitle_transcript",
        lambda url: [{"time": 1.0, "text": "接口级字幕"}],
    )

    created = client.post("/api/transcribe", json={"videoId": "BV1GJ411x7h7", "video": VIDEO_META})
    assert created.status_code == 200
    body = created.json()
    assert set(body.keys()) >= {"taskId", "status", "progress"}
    assert body["status"] == "processing"

    detail = client.get(f"/api/transcribe/{body['taskId']}")
    assert detail.status_code == 200
    data = detail.json()
    assert data["status"] == "completed"
    assert data["video"]["title"] == "测试视频"  # 元数据透传（刷新恢复用）
    assert data["transcript"] == [{"time": 1.0, "text": "接口级字幕"}]
    assert data["plainText"] == "接口级字幕"


@pytest.mark.network
def test_real_asr_on_short_video_without_subtitles():
    """无字幕短视频走真实链路：下载音频 → FFmpeg/降级 → faster-whisper。

    首次运行会下载 whisper base 模型（约 140MB），耗时较长属预期。
    """
    task_id = create_task(None)
    run_transcription_pipeline(
        task_id, REAL_NO_SUBTITLE_URL, None, prefer_subtitles=False
    )

    task = get_task(task_id)
    assert task is not None, "任务应已注册"
    assert task.status == "completed", f"任务应完成，实际：{task.status} / {task.error}"
    assert len(task.transcript) > 0, "应产出真实文字稿"
    assert all(item.time >= 0 for item in task.transcript)
    assert all(item.text for item in task.transcript)
    assert task.plain_text and len(task.plain_text) > 0
