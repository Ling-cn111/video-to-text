"""转写接口与流水线测试。

- 无标记：字幕解析 / 任务状态机 / 校验（不访问外网）
- @pytest.mark.network：真实下载音频 + faster-whisper 推理（CI 跳过，本地全量跑）
"""
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.asr import LocalWhisperEngine, postprocess_items
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


# ---- ASR 文本后处理与 initial_prompt（不访问外网）----

def test_postprocess_collapses_repeated_words_and_adds_punctuation():
    items = [
        {"time": 0.0, "text": "我我我想说一下这个"},
        {"time": 5.0, "text": "没有标点的句子"},
        {"time": 9.0, "text": "正常的谢谢大家"},  # 两次重复属正常用语，不折叠
        {"time": 12.0, "text": "已带标点。"},
    ]
    cleaned = postprocess_items(items)
    assert cleaned[0]["text"] == "我想说一下这个。"
    assert cleaned[1]["text"] == "没有标点的句子。"
    assert cleaned[2]["text"] == "正常的谢谢大家。"
    assert cleaned[3]["text"] == "已带标点。"  # 已有标点不重复追加


def test_postprocess_collapses_repeated_english_words():
    cleaned = postprocess_items([{"time": 1.0, "text": "never gonna give you the the the up"}])
    assert "the the the" not in cleaned[0]["text"]


def test_postprocess_drops_empty_texts():
    cleaned = postprocess_items([{"time": 1.0, "text": "  "}, {"time": 2.0, "text": "有效"}])
    assert len(cleaned) == 1 and cleaned[0]["text"] == "有效。"


def test_postprocess_converts_traditional_to_simplified():
    cleaned = postprocess_items([{"time": 1.0, "text": "語音轉文字"}])
    assert cleaned[0]["text"] == "语音转文字。"


def test_local_engine_passes_initial_prompt_and_hotwords(monkeypatch):
    """initial_prompt 与 hotwords 应组合后透传给 faster-whisper。"""

    class FakeInfo:
        duration = 0

    recorded: list[dict] = []

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            recorded.append(kwargs)
            return iter([]), FakeInfo()

    engine = LocalWhisperEngine()
    engine._model = FakeModel()  # noqa: SLF001 测试受控注入，跳过模型加载
    monkeypatch.setattr(
        "app.services.asr.decode_audio_16k_mono", lambda p: np.zeros(1600, dtype="float32")
    )

    engine.transcribe(
        "fake.wav",
        language="zh",
        initial_prompt="【测试】量子计算入门",
        hotwords=["Faster Whisper", "B站"],
    )

    assert len(recorded) >= 1
    prompt = recorded[0].get("initial_prompt", "")
    assert "Faster Whisper" in prompt and "B站" in prompt and "量子计算" in prompt


def test_qwen3_engine_context_and_stamps(monkeypatch):
    """Qwen3 引擎：hotwords+标题经 context 注入；time_stamps 正确转 transcript。"""
    from app.services.asr import Qwen3ASREngine

    class FakeStamp:
        def __init__(self, start_time, text):
            self.start_time = start_time
            self.text = text

    class FakeRecord:
        language = "Chinese"
        text = "测试全文"
        time_stamps = [FakeStamp(0.5, "语音"), FakeStamp(2.0, "转写")]

    recorded: list[dict] = []

    class FakeModel:
        def transcribe(self, **kwargs):
            recorded.append(kwargs)
            return [FakeRecord()]

    engine = Qwen3ASREngine()
    engine._model = FakeModel()  # noqa: SLF001 测试受控注入，跳过模型下载

    items = engine.transcribe(
        "fake.wav", language="zh", initial_prompt="标题", hotwords=["Qwen3-ASR"]
    )

    assert recorded[0]["context"] == "Qwen3-ASR，标题"
    assert recorded[0]["language"] == "Chinese"
    assert recorded[0]["return_time_stamps"] is True
    assert items == [
        {"time": 0.5, "text": "语音。"},
        {"time": 2.0, "text": "转写。"},
    ]


def test_qwen3_engine_falls_back_to_full_text_without_stamps(monkeypatch):
    """对齐器未产出时间戳时保底整段单条。"""
    from app.services.asr import Qwen3ASREngine

    class FakeRecord:
        language = "Chinese"
        text = "整段全文"
        time_stamps = []

    class FakeModel:
        def transcribe(self, **kwargs):
            return [FakeRecord()]

    engine = Qwen3ASREngine()
    engine._model = FakeModel()  # noqa: SLF001
    items = engine.transcribe("fake.wav")
    assert items == [{"time": 0.0, "text": "整段全文。"}]


def test_transcribe_audio_falls_back_when_primary_fails(monkeypatch):
    """主引擎失败 → 按 ASR_ENGINE_FALLBACK 降级到备用引擎。"""
    from app.services import asr as asr_mod

    class FailingEngine:
        name = "qwen3"

        def transcribe(self, *args, **kwargs):
            raise RuntimeError("qwen3 boom")

    class OkEngine:
        name = "faster-whisper"

        def transcribe(self, *args, **kwargs):
            return [{"time": 0.0, "text": "降级成功"}]

    monkeypatch.setattr(asr_mod, "get_engine", lambda name=None: FailingEngine())
    monkeypatch.setattr(asr_mod, "ASR_ENGINE_FALLBACK", "faster-whisper")
    monkeypatch.setattr(asr_mod, "build_engine", lambda name: OkEngine())

    assert asr_mod.transcribe_audio("fake.wav") == [{"time": 0.0, "text": "降级成功"}]


def test_transcribe_audio_raises_primary_error_when_fallback_fails(monkeypatch):
    """降级也失败时抛主引擎的原始错误。"""
    from app.services import asr as asr_mod

    class FailingEngine:
        name = "qwen3"

        def transcribe(self, *args, **kwargs):
            raise RuntimeError("qwen3 boom")

    class AlsoFailing:
        name = "faster-whisper"

        def transcribe(self, *args, **kwargs):
            raise RuntimeError("fallback boom")

    monkeypatch.setattr(asr_mod, "get_engine", lambda name=None: FailingEngine())
    monkeypatch.setattr(asr_mod, "ASR_ENGINE_FALLBACK", "faster-whisper")
    monkeypatch.setattr(asr_mod, "build_engine", lambda name: AlsoFailing())

    with pytest.raises(RuntimeError, match="qwen3 boom"):
        asr_mod.transcribe_audio("fake.wav")


def test_transcribe_audio_no_fallback_configured(monkeypatch):
    """ASR_ENGINE_FALLBACK 留空时直接抛主引擎错误。"""
    from app.services import asr as asr_mod

    class FailingEngine:
        name = "qwen3"

        def transcribe(self, *args, **kwargs):
            raise RuntimeError("qwen3 boom")

    monkeypatch.setattr(asr_mod, "get_engine", lambda name=None: FailingEngine())
    monkeypatch.setattr(asr_mod, "ASR_ENGINE_FALLBACK", "")

    with pytest.raises(RuntimeError, match="qwen3 boom"):
        asr_mod.transcribe_audio("fake.wav")


# ---- 本地大模型引擎真实推理（需下载模型权重，CI 跳过）----

REAL_BENCHMARK_WAV_SCRIPT = None  # 占位：真实音频由 benchmark 的 prepare_audio 提供


@pytest.mark.asr_heavy
def test_qwen3_engine_real_transcription():
    """Qwen3-ASR-1.7B 真实转写：150 秒无字幕中文视频 → 带时间戳文字稿。"""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.benchmark_asr import prepare_audio, BENCHMARK_DIR

    from app.services.asr import Qwen3ASREngine

    case_url = "https://www.bilibili.com/video/av116187438517161"
    wav = prepare_audio(case_url, 150, BENCHMARK_DIR.parent.parent / ".audio-bench" / "qwen3-test")

    engine = Qwen3ASREngine()
    items = engine.transcribe(wav, language="zh", initial_prompt="生理科普短视频")

    assert len(items) > 0, "应产出真实文字稿"
    assert all(item["text"] for item in items)
    assert all(item["time"] >= 0 for item in items)


@pytest.mark.asr_heavy
def test_funasr_nano_engine_real_transcription():
    """Fun-ASR-Nano 真实转写：输出整段文本（无时间戳属已知边界）。"""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.benchmark_asr import prepare_audio, BENCHMARK_DIR

    from app.services.asr import FunASRNanoEngine

    case_url = "https://www.bilibili.com/video/av116187438517161"
    wav = prepare_audio(case_url, 150, BENCHMARK_DIR.parent.parent / ".audio-bench" / "funasr-test")

    engine = FunASRNanoEngine()
    items = engine.transcribe(wav, language="zh")

    assert len(items) >= 1
    assert items[0]["text"], "Fun-ASR-Nano 应产出真实文字稿"


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
