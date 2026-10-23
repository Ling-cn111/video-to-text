"""音频获取（S1-B 去磁盘化）：yt-dlp 解析直链 → httpx 流式拉取 → BytesIO。

- download_audio(url) -> BytesIO：默认不写磁盘——extract_info(download=False) 取音频
  直链与 http_headers，httpx 流式拉入内存；直链不可用（多路/DASH 等）时回退 yt-dlp
  下载到系统临时目录，读入内存后目录自动清理
- 大小上限 MAX_AUDIO_SIZE_MB（默认 100）：下载前按 content-length 预检 + 边拉边累计，
  超限抛 AUDIO_TOO_LARGE，绝不整块吃内存
- convert_to_wav_16k_mono(audio) -> BytesIO：FFmpeg 管道（pipe:0 → pipe:1）转
  16kHz 单声道 WAV，Popen + communicate(input=...) 全量交互（防大输入管道死锁）；
  FFmpeg 未安装时原样返回（ASR 侧走 PyAV 解码兜底）
"""
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
import yt_dlp

from app.config import MAX_AUDIO_SIZE_MB
from app.errors import AudioTooLargeError, ParseFailedError
from app.services.ytdlp_options import base_options as _base_options

_CHUNK_SIZE = 64 * 1024
_DOWNLOAD_TIMEOUT = 300.0
_FFMPEG_TIMEOUT = 600


def check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _size_limit_bytes() -> int:
    return MAX_AUDIO_SIZE_MB * 1024 * 1024


def download_audio(url: str) -> io.BytesIO:
    """下载视频最佳音轨到内存（BytesIO），不落工作目录。"""
    limit = _size_limit_bytes()
    options = {
        # 公共项（no_color / Referer / socket_timeout 等）见 ytdlp_options.py
        **_base_options(),
        "format": "bestaudio/best",
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise ParseFailedError(f"音频下载失败：{exc}") from exc

    if not info:
        raise ParseFailedError("音频下载失败：未获取到音频信息")

    direct_url = info.get("url")
    if isinstance(direct_url, str) and direct_url.startswith(("http://", "https://")):
        try:
            return _fetch_direct(str(direct_url), info.get("http_headers") or {}, limit)
        except AudioTooLargeError:
            raise  # 明确超限：直接报错，不再回退
        except (httpx.HTTPError, OSError, ValueError):
            pass  # 直链拉取失败（含 HLS 分片等）→ 回退临时目录下载

    return _download_via_tempdir(url, limit)


def _fetch_direct(direct_url: str, headers: dict, limit: int) -> io.BytesIO:
    """httpx 流式拉取直链到 BytesIO；超限即中断（不整块读入）。"""
    buffer = io.BytesIO()
    total = 0
    with httpx.stream(
        "GET", direct_url, headers=headers, timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True
    ) as response:
        response.raise_for_status()
        # HLS/DASH 清单（非单一音频文件）无法直接当音频用：抛 ValueError 触发临时目录回退
        content_type = response.headers.get("content-type", "").lower()
        if "mpegurl" in content_type or "dash+xml" in content_type:
            raise ValueError("直链为流媒体清单，回退 yt-dlp 下载")
        length = response.headers.get("content-length")
        if length and length.isdigit() and int(length) > limit:
            raise AudioTooLargeError()
        for chunk in response.iter_bytes(_CHUNK_SIZE):
            total += len(chunk)
            if total > limit:
                raise AudioTooLargeError()
            buffer.write(chunk)
    if total == 0:
        raise ParseFailedError("音频下载失败：未获取到音频数据")
    buffer.seek(0)
    return buffer


def _download_via_tempdir(url: str, limit: int) -> io.BytesIO:
    """回退路径：yt-dlp 下载到系统临时目录 → 读入内存 → 目录自动删除。"""
    with tempfile.TemporaryDirectory(prefix="vtt-audio-") as tmp:
        options = {
            # 公共项（no_color / Referer / socket_timeout 等）见 ytdlp_options.py
            **_base_options(),
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmp, "%(id)s.%(ext)s"),
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
            candidates = [p for p in Path(tmp).glob("*") if p.is_file()]
            if not candidates:
                raise ParseFailedError("音频下载失败：未找到下载文件")
            path = candidates[0]

        if path.stat().st_size > limit:
            raise AudioTooLargeError()
        buffer = io.BytesIO(path.read_bytes())
    buffer.seek(0)
    return buffer


def convert_to_wav_16k_mono(audio: io.BytesIO) -> io.BytesIO:
    """FFmpeg 管道转 16kHz 单声道 WAV：BytesIO 进 → BytesIO 出。

    防死锁（S1-B 明确要求）：Popen(stdin/stdout/stderr=PIPE) + communicate(input=data)
    全量交互——stderr 由 communicate 一并消费，严禁手动 write/read（大输入必死锁）。
    FFmpeg 未安装时原样返回（ASR 侧 PyAV 解码兜底）。
    """
    audio.seek(0)
    data = audio.getvalue()
    if not check_ffmpeg():
        return io.BytesIO(data)

    proc = subprocess.Popen(
        [
            "ffmpeg", "-y",
            "-i", "pipe:0",
            "-ac", "1",      # 单声道
            "-ar", "16000",  # 16kHz
            "-f", "wav",
            "pipe:1",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, _stderr = proc.communicate(input=data, timeout=_FFMPEG_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.communicate()
        raise ParseFailedError("音频转换失败，请稍后重试") from exc
    if proc.returncode != 0:
        raise ParseFailedError("音频转换失败，请稍后重试")
    return io.BytesIO(stdout)
