"""yt-dlp 解析服务：把 B站（后续扩展更多平台）视频页解析为 VideoInfo。

安全前提：调用前 URL 已通过 url_guard（http/https + 非保留地址）与平台白名单校验。
"""
import yt_dlp

from app.errors import ParseFailedError, UnsupportedPlatformError
from app.platforms import PlatformMeta, detect_platform, extract_video_id
from app.schemas import VideoInfo
from app.url_guard import validate_public_http_url

_YDL_OPTIONS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "noplaylist": True,
    # 只取元数据，不解析分片下载地址，速度更快
    "extract_flat": False,
}


def parse_video_url(raw_url: str) -> VideoInfo:
    """入口：校验 → 平台识别 → yt-dlp 提取 → 契约模型。"""
    if not raw_url or not raw_url.strip():
        raise ParseFailedError("请输入视频链接")

    parsed = validate_public_http_url(raw_url)
    meta: PlatformMeta | None = detect_platform(parsed.hostname or "")
    if meta is None:
        raise UnsupportedPlatformError(
            f"暂不支持「{parsed.hostname}」平台，当前支持哔哩哔哩"
        )

    url_video_id = extract_video_id(parsed.path, meta)

    try:
        with yt_dlp.YoutubeDL(_YDL_OPTIONS) as ydl:
            info = ydl.extract_info(raw_url.strip(), download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise ParseFailedError(f"视频解析失败：{exc}") from exc

    if not info:
        raise ParseFailedError("视频解析失败：未获取到视频信息")

    title = str(info.get("title") or "").strip()
    if not title:
        raise ParseFailedError("视频解析失败：未获取到标题")

    cover = info.get("thumbnail") or ""
    if not cover:
        thumbnails = info.get("thumbnails") or []
        cover = str(thumbnails[-1].get("url", "")) if thumbnails else ""
    # B站 CDN 支持 https：统一升级，避免 https 站点引用 http 图片被混合内容策略拦截
    if cover.startswith("http://"):
        cover = f"https://{cover[len('http://'):]}"

    return VideoInfo(
        title=title,
        cover=cover,
        duration=int(info.get("duration") or 0),
        platform=meta.id,
        videoId=str(info.get("id") or url_video_id or ""),
    )
