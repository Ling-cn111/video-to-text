"""人声分离（Demucs，可选依赖）：强 BGM 音频的转写优化。

仅在安装了 requirements-separation.txt（demucs/torch）时可用；未安装时调用会抛
ParseFailedError，由调用方降级回原音频。产出的 vocals 显式重采样为
16kHz 单声道 WAV，再经 decode_audio_16k_mono 解码为 float32——与阶段二的
PyAV 自定义解码链保持同一格式契约（dtype=float32、单声道、16kHz）。

触发策略（见 should_separate）：
- ENABLE_VOCAL_SEPARATION=true：无条件分离
- 否则：VAD 统计非语音时长占比 > VOCAL_SEP_TRIGGER_RATIO（默认 0.4）时自动分离
- 纯音乐 MV 场景：分离后仍走 transcribe 的「VAD 失败自动去 VAD 重试」兜底链，
  Demucs 不绕过该兜底
"""
import shutil
import subprocess
from pathlib import Path

from app.config import ENABLE_VOCAL_SEPARATION, VOCAL_SEP_TRIGGER_RATIO
from app.errors import ParseFailedError

DEMUCS_MODEL = "htdemucs"


def is_demucs_available() -> bool:
    """demucs/torch 是否已安装（可选依赖）。"""
    import importlib.util

    return importlib.util.find_spec("demucs") is not None


def measure_non_speech_ratio(audio_path) -> float:
    """Silero VAD 统计非语音时长占比（0~1）。解码失败按 0 处理（不触发分离）。"""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    from app.services.asr import decode_audio_16k_mono

    try:
        audio = decode_audio_16k_mono(audio_path)
    except Exception:
        return 0.0

    total = len(audio) / 16000
    if total <= 0:
        return 0.0
    timestamps = get_speech_timestamps(audio, VadOptions())
    speech = sum((ts["end"] - ts["start"]) for ts in timestamps) / 16000
    return max(0.0, min(1.0, 1 - speech / total))


def should_separate(audio_path) -> tuple[bool, float]:
    """返回 (是否分离, 非语音占比)。"""
    ratio = measure_non_speech_ratio(audio_path)
    if ENABLE_VOCAL_SEPARATION:
        return True, ratio
    return ratio > VOCAL_SEP_TRIGGER_RATIO, ratio


def separate_vocals(source: Path, out_dir: Path) -> Path:
    """运行 Demucs（htdemucs，two-stems）分离出人声，并重采样为 16kHz 单声道 WAV。

    返回 vocals-16k.wav 路径；demucs 未安装或运行失败抛 ParseFailedError，
    由调用方降级回原音频。
    """
    if not is_demucs_available():
        raise ParseFailedError("人声分离组件未安装（requirements-separation.txt）")

    out_dir.mkdir(parents=True, exist_ok=True)
    result = out_dir / "separated" / DEMUCS_MODEL / source.stem / "vocals.wav"
    if not result.exists():
        try:
            subprocess.run(
                [
                    sys_executable(), "-m", "demucs.separate",
                    "-n", DEMUCS_MODEL,
                    "--two-stems", "vocals",
                    "-o", str(out_dir / "separated"),
                    str(source),
                ],
                check=True,
                capture_output=True,
                timeout=1800,
            )
        except subprocess.TimeoutExpired as exc:
            raise ParseFailedError("人声分离超时，请稍后重试") from exc
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.decode("utf-8", "ignore")[-200:] if exc.stderr else ""
            raise ParseFailedError(f"人声分离失败：{detail}") from exc

    if not result.exists():
        raise ParseFailedError("人声分离失败：未找到 vocals 输出")

    # 显式重采样到 16kHz 单声道（与 ASR 解码链的格式契约一致）
    vocals_16k = out_dir / "vocals-16k.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(result), "-ac", "1", "-ar", "16000", str(vocals_16k)],
            check=True,
            capture_output=True,
            timeout=600,
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise ParseFailedError("人声重采样失败，请稍后重试") from exc
    return vocals_16k


def sys_executable() -> str:
    import sys

    return sys.executable


def probe_wav_format(path: Path) -> dict:
    """探测 WAV 格式（sample_rate / channels），供格式断言使用。"""
    import av

    container = av.open(str(path))
    try:
        stream = next(s for s in container.streams if s.type == "audio")
        return {"sample_rate": stream.codec_context.sample_rate, "channels": stream.codec_context.channels}
    finally:
        container.close()
