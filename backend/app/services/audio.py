"""音频获取：yt-dlp 下载音轨 + FFmpeg 转 16kHz 单声道 WAV。

FFmpeg 未安装时降级：直接把原始音频交给 ASR（faster-whisper 内置 PyAV 解码）。
"""
import shutil
import subprocess
from pathlib import Path

import yt_dlp

from app.errors import ParseFailedError


def check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def download_audio(url: str, dest_dir: Path) -> Path:
    """下载视频的最佳音轨，返回本地文件路径。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    options = {
        "format": "bestaudio/best",
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise ParseFailedError(f"音频下载失败：{exc}") from exc

    if not info:
        raise ParseFailedError("音频下载失败：未获取到音频信息")

    path = Path(ydl.prepare_filename(info))
    if not path.exists():
        # prepare_filename 可能带流后缀，做一次模糊匹配兜底
        candidates = list(dest_dir.glob(f"{info.get('id', '*')}.*"))
        if not candidates:
            raise ParseFailedError("音频下载失败：未找到下载文件")
        path = candidates[0]
    return path


def convert_to_wav_16k_mono(source: Path, dest_dir: Path) -> Path:
    """FFmpeg 转 16kHz 单声道 WAV（ASR 标准输入）。FFmpeg 缺失时原样返回。"""
    if not check_ffmpeg():
        return source

    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{source.stem}-16k.wav"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(source),
                "-ac", "1",      # 单声道
                "-ar", "16000",  # 16kHz
                str(target),
            ],
            check=True,
            capture_output=True,
            timeout=600,
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise ParseFailedError("音频转换失败，请稍后重试") from exc
    return target
