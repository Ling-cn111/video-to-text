"""人声分离测试。

- 无标记：触发阈值逻辑（不依赖 Demucs，纯逻辑）
- @pytest.mark.separation：需要可选依赖 Demucs/torch（requirements-separation.txt），
  首次运行会下载 htdemucs 模型（约 80MB）；CI 默认跳过（pytest -m "not network and not separation"）
"""
import math
import wave
from pathlib import Path

import numpy as np
import pytest

from app.services import separation
from app.services.asr import decode_audio_16k_mono

pytestmark_separation = pytest.mark.separation


def write_sine_wav(path: Path, seconds: float = 2.0, sample_rate: int = 44100) -> Path:
    """生成双声道 44.1kHz 的混合正弦波 WAV（模拟音乐输入）。"""
    frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        for i in range(frames):
            t = i / sample_rate
            left = int(12000 * math.sin(2 * math.pi * 220 * t))
            right = int(9000 * math.sin(2 * math.pi * 330 * t + 0.5))
            wav.writeframesraw(left.to_bytes(2, "little", signed=True) + right.to_bytes(2, "little", signed=True))
    return path


# ---- 触发阈值逻辑（纯逻辑，不依赖 Demucs）----

def test_should_separate_always_when_enabled(monkeypatch):
    monkeypatch.setattr(separation, "ENABLE_VOCAL_SEPARATION", True)
    monkeypatch.setattr(separation, "measure_non_speech_ratio", lambda p: 0.05)
    assert separation.should_separate("any.wav") == (True, 0.05)


def test_should_separate_by_threshold(monkeypatch):
    monkeypatch.setattr(separation, "ENABLE_VOCAL_SEPARATION", False)
    monkeypatch.setattr(separation, "VOCAL_SEP_TRIGGER_RATIO", 0.4)

    monkeypatch.setattr(separation, "measure_non_speech_ratio", lambda p: 0.6)
    assert separation.should_separate("bgm.wav")[0] is True  # 非语音 60% > 40% → 分离

    monkeypatch.setattr(separation, "measure_non_speech_ratio", lambda p: 0.2)
    assert separation.should_separate("clear.wav")[0] is False  # 清晰语音 → 跳过


def test_should_separate_pure_music_triggers(monkeypatch):
    """纯音乐 MV：VAD 全滤（非语音占比 1.0）必然触发分离。"""
    monkeypatch.setattr(separation, "ENABLE_VOCAL_SEPARATION", False)
    monkeypatch.setattr(separation, "measure_non_speech_ratio", lambda p: 1.0)
    assert separation.should_separate("mv.wav")[0] is True


# ---- Demucs 分离与格式契约（需可选依赖）----

@pytest.mark.separation
def test_separated_vocals_format_contract(tmp_path: Path):
    """分离输出显式重采样为 16kHz 单声道 WAV；解码后为 float32 单声道——与 PyAV 解码链一致。"""
    source = write_sine_wav(tmp_path / "input.wav")
    vocals = separation.separate_vocals(source, tmp_path)

    fmt = separation.probe_wav_format(vocals)
    assert fmt["sample_rate"] == 16000
    assert fmt["channels"] == 1

    audio = decode_audio_16k_mono(vocals)
    assert audio.dtype == np.float32
    assert audio.ndim == 1  # 单声道
    assert 0 < len(audio) / 16000 <= 2.5  # 时长与输入对齐（允许分离模型裁剪静音）


@pytest.mark.separation
def test_demucs_unavailable_raises_friendly_error(monkeypatch):
    monkeypatch.setattr(separation, "is_demucs_available", lambda: False)
    with pytest.raises(Exception, match="人声分离组件未安装"):
        separation.separate_vocals(Path("x.wav"), Path("out"))
