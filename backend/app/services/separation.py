"""人声分离（Demucs，可选依赖）：强 BGM 音频的转写优化。

仅在安装了 requirements-separation.txt（demucs/torch）时可用；未安装时调用会抛
ParseFailedError，由调用方降级回原音频。产出的 vocals 显式重采样为
16kHz 单声道 WAV，再经 decode_audio_16k_mono 解码为 float32——与阶段二的
PyAV 自定义解码链保持同一格式契约（dtype=float32、单声道、16kHz）。

去磁盘化（S1-B）：入口/出口均为内存 BytesIO；demucs 只认文件路径，故内部落盘到
系统临时目录（TemporaryDirectory），读完输出后自动清理，无持久残留。

触发策略（见 should_separate）：
- ENABLE_VOCAL_SEPARATION=true：无条件分离
- 否则：VAD 统计非语音时长占比 > VOCAL_SEP_TRIGGER_RATIO（默认 0.4）时自动分离
- 纯音乐 MV 场景：分离后仍走 transcribe 的「VAD 失败自动去 VAD 重试」兜底链，
  Demucs 不绕过该兜底
"""
import io
import subprocess
import tempfile
from pathlib import Path

from app.config import ENABLE_VOCAL_SEPARATION, VOCAL_SEP_TRIGGER_RATIO
from app.errors import ParseFailedError

DEMUCS_MODEL = "htdemucs"


def is_demucs_available() -> bool:
    """demucs/torch 是否已安装（可选依赖）。"""
    import importlib.util

    return importlib.util.find_spec("demucs") is not None


def measure_non_speech_ratio(audio) -> float:
    """Silero VAD 统计非语音时长占比（0~1）。解码失败按 0 处理（不触发分离）。

    audio 为内存音频 BytesIO（亦兼容路径）。
    """
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    from app.services.asr import decode_audio_16k_mono

    try:
        decoded = decode_audio_16k_mono(audio)
    except Exception:
        return 0.0

    total = len(decoded) / 16000
    if total <= 0:
        return 0.0
    timestamps = get_speech_timestamps(decoded, VadOptions())
    speech = sum((ts["end"] - ts["start"]) for ts in timestamps) / 16000
    return max(0.0, min(1.0, 1 - speech / total))


def should_separate(audio) -> tuple[bool, float]:
    """返回 (是否分离, 非语音占比)。"""
    ratio = measure_non_speech_ratio(audio)
    if ENABLE_VOCAL_SEPARATION:
        return True, ratio
    return ratio > VOCAL_SEP_TRIGGER_RATIO, ratio


def separate_vocals(audio) -> io.BytesIO:
    """运行 Demucs（htdemucs，two-stems）分离出人声，重采样为 16kHz 单声道 WAV 返回。

    S1-B 去磁盘化：入参为内存音频（BytesIO，兼容路径），内部落盘到系统临时目录供
    demucs 使用，输出读回内存后临时目录自动清理；demucs 未安装或运行失败抛
    ParseFailedError，由调用方降级回原音频。
    """
    if not is_demucs_available():
        raise ParseFailedError("人声分离组件未安装（requirements-separation.txt）")

    if hasattr(audio, "seek"):
        audio.seek(0)
        data = audio.getvalue()
    else:
        data = Path(audio).read_bytes()

    with tempfile.TemporaryDirectory(prefix="vtt-sep-") as tmp:
        work_dir = Path(tmp)
        source = work_dir / "input.wav"
        source.write_bytes(data)

        result = work_dir / "separated" / DEMUCS_MODEL / source.stem / "vocals.wav"
        try:
            subprocess.run(
                [
                    sys_executable(), "-m", "demucs.separate",
                    "-n", DEMUCS_MODEL,
                    "--two-stems", "vocals",
                    "-o", str(work_dir / "separated"),
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

        # 显式重采样到 16kHz 单声道（与 ASR 解码链的格式契约一致），直接输出到内存
        try:
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", str(result), "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1"],
                check=True,
                capture_output=True,
                timeout=600,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            raise ParseFailedError("人声重采样失败，请稍后重试") from exc
        buffer = io.BytesIO(proc.stdout)

    buffer.seek(0)
    return buffer


def sys_executable() -> str:
    import sys

    return sys.executable


def probe_wav_format(audio) -> dict:
    """探测 WAV 格式（sample_rate / channels），供格式断言使用。接受 BytesIO 或路径。"""
    import av

    if hasattr(audio, "seek"):
        audio.seek(0)
        source = audio
    else:
        source = str(audio)
    container = av.open(source)
    try:
        stream = next(s for s in container.streams if s.type == "audio")
        return {"sample_rate": stream.codec_context.sample_rate, "channels": stream.codec_context.channels}
    finally:
        container.close()
