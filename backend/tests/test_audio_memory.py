"""音频去磁盘化（S1-B）测试：下载 → 转换 → ASR → 清理，全程内存。

覆盖：
- download_audio 走直链 httpx 流式拉取 → BytesIO，不落磁盘（含 http_headers 透传）
- 音频大小上限（MAX_AUDIO_SIZE_MB）→ AUDIO_TOO_LARGE 友好中文错误
- FFmpeg 管道转换：> 系统管道 buffer（64KB）的输入不卡死且转换成功
- 只认路径的引擎（qwen3）落盘临时文件，用完即删（断言路径已不存在）
- 流水线跑完系统临时目录零 vtt-* 残留
- 云端 ASR 直接以内存 bytes 上传（不读磁盘）
"""
import io
import struct
import tempfile
import wave
from pathlib import Path

import pytest

from app.services import audio as audio_module
from app.services import separation
from app.services.asr import CloudASREngine, Qwen3ASREngine, _materialized_audio
from app.services.transcribe import run_transcription_pipeline

# 系统管道 buffer 通常 64KB；超过它才能暴露「手动 write/read」式死锁
_PIPE_BUFFER = 64 * 1024


def _wav_bytes(seconds: float = 1.0, rate: int = 16000, channels: int = 1) -> bytes:
    """生成 16-bit PCM WAV bytes（静音偏置），用于内存音频流程测试。"""
    frames = int(seconds * rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack("<" + "h" * frames, *([1000] * frames)))
    return buffer.getvalue()


# ---- download_audio：直链拉取入内存 ----


class _FakeYDL:
    """yt-dlp 替身：extract_info(download=False) 返回音频直链信息。"""

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        return {
            "url": "https://cdn.test/audio.m4a",
            "http_headers": {"Referer": "https://www.bilibili.com/"},
        }


class _FakeStreamResponse:
    def __init__(self, chunks, headers=None):
        self._chunks = chunks
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self, chunk_size):
        yield from self._chunks


def test_download_audio_direct_link_to_memory(monkeypatch):
    """直链经 httpx 流式拉入 BytesIO：内容正确、headers 透传、不写磁盘。"""
    monkeypatch.setattr(audio_module.yt_dlp, "YoutubeDL", _FakeYDL)

    captured: dict = {}

    def fake_stream(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return _FakeStreamResponse([b"abc", b"def"], headers={"content-length": "6"})

    monkeypatch.setattr(audio_module.httpx, "stream", fake_stream)

    buffer = audio_module.download_audio("https://www.bilibili.com/video/BV1x")

    assert isinstance(buffer, io.BytesIO)
    assert buffer.getvalue() == b"abcdef"
    assert captured["method"] == "GET"
    assert captured["url"] == "https://cdn.test/audio.m4a"
    assert captured["headers"] == {"Referer": "https://www.bilibili.com/"}  # 防盗链头透传


def test_download_audio_rejects_oversize_by_content_length(monkeypatch):
    """content-length 预检即超限：抛 AUDIO_TOO_LARGE，不把内容读进内存。"""
    monkeypatch.setattr(audio_module.yt_dlp, "YoutubeDL", _FakeYDL)
    monkeypatch.setattr(audio_module, "MAX_AUDIO_SIZE_MB", 0)  # 上限压到 0，任何字节都超

    def fake_stream(method, url, **kwargs):
        return _FakeStreamResponse([b"x"], headers={"content-length": "10"})

    monkeypatch.setattr(audio_module.httpx, "stream", fake_stream)

    with pytest.raises(Exception) as exc_info:
        audio_module.download_audio("https://www.bilibili.com/video/BV1x")
    assert exc_info.value.code == "AUDIO_TOO_LARGE"
    assert exc_info.value.message == "音频过大，暂不支持"


def test_download_audio_rejects_oversize_while_streaming(monkeypatch):
    """无 content-length 时边拉边累计，超限即中断。"""
    monkeypatch.setattr(audio_module.yt_dlp, "YoutubeDL", _FakeYDL)
    monkeypatch.setattr(audio_module, "MAX_AUDIO_SIZE_MB", 0)

    def fake_stream(method, url, **kwargs):
        return _FakeStreamResponse([b"x" * 2048])  # 无 content-length

    monkeypatch.setattr(audio_module.httpx, "stream", fake_stream)

    with pytest.raises(Exception) as exc_info:
        audio_module.download_audio("https://www.bilibili.com/video/BV1x")
    assert exc_info.value.code == "AUDIO_TOO_LARGE"


# ---- FFmpeg 管道：大输入不死锁 ----


@pytest.mark.skipif(not audio_module.check_ffmpeg(), reason="需要 FFmpeg")
def test_ffmpeg_pipe_handles_large_input_without_deadlock():
    """输入 > 64KB（超管道 buffer）：communicate 全量交互，转换成功且不卡死。"""
    source = _wav_bytes(seconds=5.0, rate=16000, channels=1)  # 约 160KB
    assert len(source) > _PIPE_BUFFER

    converted = audio_module.convert_to_wav_16k_mono(io.BytesIO(source))
    data = converted.getvalue()

    assert data[:4] == b"RIFF"
    with wave.open(io.BytesIO(data)) as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.getnframes() > 0


# ---- 只认路径的引擎：落盘临时文件用完即删 ----


def test_materialized_audio_removes_temp_file_after_use():
    audio = io.BytesIO(b"payload-bytes")
    with _materialized_audio(audio) as temp_path:
        assert temp_path.exists()
        assert temp_path.read_bytes() == b"payload-bytes"
    assert not temp_path.exists()  # 退出即删


def test_qwen3_engine_cleans_up_temp_file_after_transcribe():
    """qwen3 引擎内部把内存音频落盘给模型，转写完成后临时文件不存在。"""
    captured: dict = {}

    class FakeRecord:
        text = "整段全文"
        time_stamps = []

    class FakeModel:
        def transcribe(self, **kwargs):
            path = Path(kwargs["audio"])
            assert path.exists()  # 转写进行时临时文件存在
            captured["path"] = path
            return [FakeRecord()]

    engine = Qwen3ASREngine()
    engine._model = FakeModel()  # noqa: SLF001 测试受控注入

    items = engine.transcribe(io.BytesIO(b"fake-audio"))

    assert items == [{"time": 0.0, "text": "整段全文。"}]
    assert not captured["path"].exists()  # 转写结束即清理


# ---- 云端 ASR：直接以 bytes 上传 ----


def test_cloud_upload_uses_memory_bytes(monkeypatch):
    monkeypatch.setattr("app.services.asr.CLOUD_ASR_API_KEY", "test-key")
    monkeypatch.setattr("app.services.asr.CLOUD_ASR_BASE_URL", "https://api.siliconflow.cn/v1")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"segments": [{"start": 0.0, "text": "你好"}]}

    captured: dict = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("app.services.asr.httpx.post", fake_post)

    CloudASREngine().transcribe(io.BytesIO(b"payload"))

    filename, content = captured["files"]["file"]
    assert filename == "audio.wav"
    assert content == b"payload"  # 直接上传内存 bytes，无磁盘读取


# ---- 流水线：零临时文件残留 ----


def _vtt_temp_entries() -> set[str]:
    return {path.name for path in Path(tempfile.gettempdir()).glob("vtt-*")}


def test_pipeline_leaves_no_temp_residue(monkeypatch):
    """下载（mock 内存）→ 转换 → ASR（mock）全程后，系统临时目录无 vtt-* 残留。"""
    from app.services import transcribe as transcribe_module
    from app.services.tasks import create_task, get_task

    monkeypatch.setattr(transcribe_module, "download_audio", lambda url: io.BytesIO(_wav_bytes(1.0)))
    monkeypatch.setattr(transcribe_module, "try_extract_subtitle_transcript", lambda url: None)
    monkeypatch.setattr("app.services.separation.is_demucs_available", lambda: False)
    monkeypatch.setattr(
        transcribe_module.asr, "transcribe_audio", lambda *args, **kwargs: [{"time": 0.0, "text": "你好"}]
    )

    before = _vtt_temp_entries()
    task_id = create_task(None)
    run_transcription_pipeline(task_id, "https://example.test/video", None, prefer_subtitles=False)

    task = get_task(task_id)
    assert task.status == "completed"
    assert task.plain_text == "你好"
    assert _vtt_temp_entries() == before  # 无任何 vtt-* 临时文件残留


def test_separation_available_but_not_triggered_stays_in_memory(monkeypatch):
    """demucs 可用但未触发分离时，pipeline 不产生临时文件。"""
    from app.services import transcribe as transcribe_module
    from app.services.tasks import create_task, get_task

    monkeypatch.setattr(transcribe_module, "download_audio", lambda url: io.BytesIO(_wav_bytes(1.0)))
    monkeypatch.setattr(transcribe_module, "try_extract_subtitle_transcript", lambda url: None)
    monkeypatch.setattr(separation, "is_demucs_available", lambda: True)
    monkeypatch.setattr(separation, "should_separate", lambda audio: (False, 0.1))
    monkeypatch.setattr(
        transcribe_module.asr, "transcribe_audio", lambda *args, **kwargs: [{"time": 0.0, "text": "内容"}]
    )

    before = _vtt_temp_entries()
    task_id = create_task(None)
    run_transcription_pipeline(task_id, "https://example.test/video", None, prefer_subtitles=False)

    assert get_task(task_id).status == "completed"
    assert _vtt_temp_entries() == before
