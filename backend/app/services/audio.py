"""音频获取（S1-B 去磁盘化）：B站 playurl API 取直链 → httpx 流式拉取 → BytesIO。

- download_audio(url) -> BytesIO：默认不写磁盘——先走 B站 playurl API 拿 DASH
  音频直链（纯 JSON，不碰网页），httpx 流式拉入内存；失败再回退 yt-dlp
  解析直链，最后回退 yt-dlp 下载到系统临时目录
- 大小上限 MAX_AUDIO_SIZE_MB（默认 100）：下载前按 content-length 预检 + 边拉边累计，
  超限抛 AUDIO_TOO_LARGE，绝不整块吃内存
- convert_to_wav_16k_mono(audio) -> BytesIO：FFmpeg 管道（pipe:0 → pipe:1）转
  16kHz 单声道 WAV，Popen + communicate(input=...) 全量交互（防大输入管道死锁）；
  FFmpeg 未安装时原样返回（ASR 侧走 PyAV 解码兜底）

## 为什么优先走 API 而不是 yt-dlp

云端实测：阿里云出口 IP 上 `www.bilibili.com/video/*` 网页被风控死锁（412，
即使带设备指纹 + 首页会话 Cookie 也无效），而 `api.bilibili.com` 的 JSON 接口
带指纹即可正常访问。yt-dlp 解析 B站必须先抓网页，所以在这类机器上必然失败。
playurl API 只返回 JSON，绕开了整个网页环节。
"""
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

import httpx
import yt_dlp

from app.config import MAX_AUDIO_SIZE_MB
from app.errors import AudioTooLargeError, ParseFailedError
from app.services.ytdlp_options import base_options as _base_options

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 64 * 1024
_DOWNLOAD_TIMEOUT = 300.0
_FFMPEG_TIMEOUT = 600


def check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _size_limit_bytes() -> int:
    return MAX_AUDIO_SIZE_MB * 1024 * 1024


def download_audio(url: str) -> io.BytesIO:
    """下载视频最佳音轨到内存（BytesIO），不落工作目录。

    三级降级：
      1. B站 playurl API（纯 JSON，机房 IP 也能用）
      2. yt-dlp 解析直链
      3. yt-dlp 下载到临时目录
    """
    limit = _size_limit_bytes()

    # 第 1 级：playurl API。只对 B站生效，其他平台直接跳过。
    try:
        return _download_via_playurl(url, limit)
    except AudioTooLargeError:
        raise  # 明确超限：直接报错，不再回退
    except Exception as exc:
        logger.info("playurl API 路径不可用，回退 yt-dlp：%s", exc)

    # 第 2 级：yt-dlp 只解析不下载
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

    # 第 3 级：临时目录
    return _download_via_tempdir(url, limit)


def _download_via_playurl(url: str, limit: int) -> io.BytesIO:
    """走 B站 playurl API 取音频直链并流式下载。

    非 B站 URL 直接抛异常（让调用方跳过本路径，不发无谓的网络请求）。
    """
    from app.services import bili_playurl
    from app.platforms import detect_platform

    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    meta = detect_platform(host)
    if meta is None or meta.id != "bilibili":
        raise ValueError(f"非 B站链接，跳过 playurl：{host}")

    bvid = _extract_bvid(parsed.path)
    if not bvid:
        raise ValueError(f"未能从路径提取 BV 号：{parsed.path}")

    stream = bili_playurl.get_audio_stream(bvid)
    logger.info(
        "playurl 命中 audio 码率=%d mime=%s 备用地址=%d",
        stream.bandwidth,
        stream.mime_type,
        len(stream.backup_urls),
    )

    # CDN 必须带 UA + Referer + Cookie（实测少一个就 403）
    headers = bili_playurl._headers()
    candidates = (stream.url,) + stream.backup_urls
    last_error: Exception | None = None
    for index, candidate in enumerate(candidates):
        try:
            return _fetch_direct(candidate, headers, limit, expect_media=True)
        except AudioTooLargeError:
            raise
        except (httpx.HTTPError, OSError, ValueError) as exc:
            last_error = exc
            logger.warning("音频地址下载失败（第 %d 个地址）：%s", index + 1, exc)
    raise ValueError(f"所有音频地址均下载失败：{last_error}")


def _extract_bvid(path: str) -> str | None:
    """从 `/video/BVxxxx` 提取 BV 号。"""
    match = re.search(r"/video/(BV[\w]+)", path, re.IGNORECASE)
    return match.group(1) if match else None


def _fetch_direct(
    direct_url: str, headers: dict, limit: int, expect_media: bool = False
) -> io.BytesIO:
    """httpx 流式拉取直链到 BytesIO；超限即中断（不整块读入）。

    expect_media=True 时要求返回的是音视频而非 HTML：CDN 缺请求头会返回
    403 + text/html，若不校验就会把错误页当成音频喂给 FFmpeg/ASR。
    """
    buffer = io.BytesIO()
    total = 0
    with httpx.stream(
        "GET", direct_url, headers=headers, timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True
    ) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        # HLS/DASH 清单（非单一音频文件）无法直接当音频用：抛 ValueError 触发临时目录回退
        if "mpegurl" in content_type or "dash+xml" in content_type:
            raise ValueError("直链为流媒体清单，回退 yt-dlp 下载")
        if expect_media and not (
            content_type.startswith("audio/") or content_type.startswith("video/")
        ):
            raise ValueError(f"直链返回的不是媒体文件（content-type={content_type or '空'}）")
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
