"""B站分享短链（b23.tv）展开成规范视频页链接。

## 为什么必须在后端做

B站 App 的「分享」给出的是 `https://b23.tv/AbCdEfG` 这类短链。后端有两处
快路径只认 `bilibili.com` 域名：

- `backend/app/services/subtitles.py` 的 `_extract_bvid`
- `backend/app/services/audio.py` 的 `_extract_bvid`

短链命中不了它们，字幕直取与 playurl 音频直取双双失效，退回 yt-dlp 下载
+ ASR。而 `parser.py` 顶部注释记录着：云端 FC 机房 IP 抓 B站 **网页** 恒定
412（即使带设备指纹与 Cookie），只有 JSON API 通。于是短链在线上几乎必然
走到「yt-dlp 抓网页 → 412 → friendly_parse_error 归类为 unable to download
webpage → 用户看到『视频不存在或链接有误』」。

**短链本身没有错**，是后端拿不到它的重定向目标。所以这里主动展开一次，
把 `b23.tv/xxx` 换成 `www.bilibili.com/video/BVxxx`，后面的 view API、
字幕快路径、playurl 快路径就都能命中。

## 为什么不在前端做

浏览器里跨域请求 `b23.tv` 拿不到 302 的 `Location`（CORS 不暴露该头），
移动端壳里更是直接被拦。前端只能猜路径末段，而短链末段不是 BV 号。

## 安全约束

只跟随**白名单域名**之间的跳转（b23.tv ↔ bilibili.com），避免短链服务被
劫持后把我们带去内网或第三方；最多 3 跳、超时 8 秒；失败一律返回 None
交回原链接——展开是优化路径，失败不能让用户看到报错。
"""
import logging
from urllib.parse import urlparse

import httpx

from app.services import ytdlp_options

logger = logging.getLogger(__name__)

# 短链服务域名（与 platforms.py 的 share_host_patterns 对应）
_SHORT_LINK_HOSTS = ("b23.tv",)

_MAX_HOPS = 3
_TIMEOUT_SECONDS = 8.0


def is_short_link(raw_url: str) -> bool:
    """是否为需要展开的分享短链。"""
    host = (urlparse(raw_url.strip()).hostname or "").lower()
    return host in _SHORT_LINK_HOSTS


def _is_hop_allowed(url: str) -> bool:
    """跳转链路上的每一跳都必须在白名单内。

    不允许「跳到任意域名再跳回来」：中间那一跳的响应体我们不看，但它会把
    请求发出去。只准许 b23.tv 与 bilibili.com 之间的跳转，范围最小。
    """
    host = (urlparse(url).hostname or "").lower()
    if host in _SHORT_LINK_HOSTS:
        return True
    return host == "bilibili.com" or host.endswith(".bilibili.com")


def _absolute(base: str, location: str) -> str:
    """把 Location 里的相对路径补成绝对地址。"""
    if location.startswith("http://") or location.startswith("https://"):
        return location
    parsed = urlparse(base)
    if location.startswith("/"):
        return f"{parsed.scheme}://{parsed.netloc}{location}"
    return f"{parsed.scheme}://{parsed.netloc}/{location}"


def expand_short_link(raw_url: str) -> str | None:
    """把 b23.tv 短链展开成 bilibili.com 视频页链接；失败返回 None。

    逐跳手动跟随（不用 `follow_redirects=True`），因为需要每一跳都过白名单
    校验；拿到终点后再判断是否落在 bilibili.com。
    """
    if not raw_url or not is_short_link(raw_url):
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com/",
    }
    cookie = ytdlp_options.cookie_header()
    if cookie:
        headers["Cookie"] = cookie

    current = raw_url.strip()
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS, headers=headers, follow_redirects=False) as client:
            for _ in range(_MAX_HOPS):
                # 短链服务对 HEAD 的响应不稳定，用 GET 但立刻关掉，不读响应体
                with client.stream("GET", current) as response:
                    location = response.headers.get("location")
                    status = response.status_code
                if status not in (301, 302, 303, 307, 308):
                    # 没有重定向：当前地址就是终点
                    break
                if not location:
                    return None
                nxt = _absolute(current, location)
                if not _is_hop_allowed(nxt):
                    logger.info("短链展开遇到非白名单跳转，放弃：%s", nxt)
                    return None
                current = nxt

            if not _is_hop_allowed(current):
                return None
            # 目标必须是真正的视频页（含 BV 号），否则展开没有意义
            if "/video/" not in urlparse(current).path:
                logger.info("短链展开结果不是视频页，放弃：%s", current)
                return None
            logger.info("短链已展开：%s → %s", raw_url.strip(), current)
            return current
    except Exception:
        logger.warning("短链展开失败，交回原链接（%s）", raw_url.strip(), exc_info=True)
        return None
