"""视频解析服务：把 B站视频页解析为 VideoInfo。

## 为什么优先走 view API

云端实测（阿里云 cn-hangzhou 出口）：`www.bilibili.com/video/*` 网页被风控死锁
（412，即使带设备指纹 + 首页会话 Cookie 也无效），而 `api.bilibili.com` 的 JSON
接口带指纹正常。yt-dlp 解析元数据要先抓网页，所以在这类机器上必然失败 —— 表现
为「有个视频能转写成功、有的直接报错」，因为只有命中字幕快路径的才绕过了 parse。

因此解析顺序是：
  1. B站 view API（纯 JSON，机房 IP 可用）
  2. yt-dlp（其他平台，或 API 拿不到时的兜底）

安全前提：调用前 URL 已通过 url_guard（http/https + 非保留地址）与平台白名单校验。
"""
import logging
import re

import yt_dlp

from app.errors import InvalidUrlError, ParseFailedError, UnsupportedPlatformError
from app.platforms import PlatformMeta, detect_platform, extract_video_id
from app.schemas import VideoInfo
from app.services import short_link
from app.services.ytdlp_options import base_options as _base_options
from app.url_guard import validate_public_http_url

logger = logging.getLogger(__name__)


def _ydl_options() -> dict:
    """每次解析时构建：设备指纹是动态的，不能在模块导入时固化。"""
    return {
        **_base_options(),
        "skip_download": True,
        # 只取元数据，不解析分片下载地址，速度更快
        "extract_flat": False,
    }


# yt-dlp 报错里的技术性前缀，如 "ERROR: [BiliBili] BV1xxx: "
_YDL_ERROR_PREFIX = re.compile(r"^ERROR:\s*\[[^\]]+\]\s*[^:]*:\s*", re.IGNORECASE)


def friendly_parse_error(raw_message: str) -> ParseFailedError:
    """把 yt-dlp 的原始报错转成对用户友好的中文提示（不外泄技术细节）。"""
    detail = _YDL_ERROR_PREFIX.sub("", raw_message.strip()).strip()
    lowered = detail.lower()
    if "404" in lowered or "not found" in lowered or "unable to download webpage" in lowered:
        return ParseFailedError("视频不存在或链接有误，请检查视频链接后重试")
    if "private" in lowered or "login" in lowered or "sign in" in lowered:
        return ParseFailedError("该视频需要登录观看，暂不支持解析")
    if "timed out" in lowered or "connection" in lowered:
        return ParseFailedError("视频源连接超时，请稍后重试")
    return ParseFailedError("视频解析失败，请确认链接是否正确后重试")


def parse_video_url(raw_url: str) -> VideoInfo:
    """入口：校验 → 平台识别 → API/yt-dlp 提取 → 契约模型。"""
    if not raw_url or not raw_url.strip():
        raise InvalidUrlError("请输入视频链接")

    parsed = validate_public_http_url(raw_url)
    meta: PlatformMeta | None = detect_platform(parsed.hostname or "")
    if meta is None:
        raise UnsupportedPlatformError(
            f"暂不支持「{parsed.hostname}」平台，当前支持哔哩哔哩"
        )

    # 分享短链（b23.tv）先展开成规范视频页：不展开的话，下面的 view API 与
    # 字幕/playurl 快路径全部命中不了，只能退回 yt-dlp 抓网页，而机房 IP 抓
    # B站网页恒定 412 —— 用户看到的是「视频不存在或链接有误」，链接其实没错。
    target_url = raw_url.strip()
    if short_link.is_short_link(target_url):
        expanded = short_link.expand_short_link(target_url)
        if expanded:
            target_url = expanded
            parsed = validate_public_http_url(target_url)
            logger.info("短链展开后解析：%s", target_url)

    url_video_id = extract_video_id(parsed.path, meta)

    # 第 1 级：B站 view API。机房 IP 上网页会 412，JSON API 不会。
    if meta.id == "bilibili" and url_video_id and url_video_id.upper().startswith("BV"):
        info = _parse_via_api(url_video_id)
        if info is not None:
            return info

    # 第 2 级：yt-dlp（其他平台，或 API 不可用时的兜底）
    try:
        with yt_dlp.YoutubeDL(_ydl_options()) as ydl:
            info = ydl.extract_info(target_url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise friendly_parse_error(str(exc)) from exc

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


def _parse_via_api(bvid: str) -> VideoInfo | None:
    """B站 view API 取元数据；任何失败返回 None 交给 yt-dlp 兜底。

    不抛异常是刻意的：这是「优化路径」，API 临时不可用（风控升级、接口变更）
    时不应让用户看到报错，回退 yt-dlp 至少在其他网络环境下还能用。
    """
    from app.services import bili_playurl

    try:
        data = bili_playurl.get_video_meta(bvid)
    except Exception:
        logger.info("view API 解析失败，回退 yt-dlp（bvid=%s）", bvid, exc_info=True)
        return None

    if not data.title:
        return None

    return VideoInfo(
        title=data.title,
        cover=data.cover,
        duration=data.duration,
        platform="bilibili",
        videoId=data.bvid,
    )
