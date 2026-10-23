"""B站设备指纹（buvid3/buvid4）获取与缓存。

## 为什么需要

云端实测（阿里云 cn-hangzhou 出口）：

    POST /internal/worker/diagnose
    {"view_api":{"status":412}, "video_page":{"status":412}, "finger_spi":{"status":200}}

业务接口全部 412，但 **设备指纹接口 200**。这说明 B站并没有封掉整台机器，
而是「没带 buvid3 的请求一律按爬虫处理」。浏览器首次访问会带上一组
`buvid3` / `buvid4`（存在 Cookie 里，有效期很长），后续请求都带着它，
所以正常用户不会遇到 412。

## 做法

未登录状态下请求 `GET /x/frontend/finger/spi`，返回 `b_3` / `b_4`，即
`buvid3` / `buvid4`。把它们拼进 Cookie 头即可，**不需要账号凭证**。

指纹按进程缓存（默认 12 小时）：有效期本身很长，频繁取反而更像爬虫。
取失败时静默返回空字符串——指纹只是「尽力而为」的缓解手段，不能让
获取失败反过来阻断转写。
"""
import logging
import threading
import time

import httpx

from app import config

logger = logging.getLogger(__name__)

FINGER_SPI_URL = "https://api.bilibili.com/x/frontend/finger/spi"

# 缓存有效期（秒）。B站 buvid 的有效期远超这个量级，这里只是避免
# 进程长期运行后用到「可能已被风控拉黑」的旧指纹。
_CACHE_TTL_SECONDS = 12 * 3600

# 进程内缓存：(值, 过期时间戳)。取失败用 (None, ...) 记短冷却，避免
# 被风控时每次请求都去打指纹接口（那本身就是可疑行为）。
_lock = threading.Lock()
_cached: tuple[str, float] | None = None
_FAILURE_COOLDOWN_SECONDS = 300


def _spi_headers() -> dict[str, str]:
    headers = {
        "User-Agent": config.BILI_USER_AGENT
        or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com/",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    return headers


def _fetch_once() -> str:
    """请求一次指纹接口，返回形如 'buvid3=xxx; buvid4=yyy' 的 Cookie 片段。"""
    response = httpx.get(FINGER_SPI_URL, headers=_spi_headers(), timeout=10)
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise ValueError(f"finger/spi 返回 code={data.get('code')}")
    payload = data.get("data") or {}
    b3 = str(payload.get("b_3") or "").strip()
    b4 = str(payload.get("b_4") or "").strip()
    if not b3:
        raise ValueError("finger/spi 未返回 b_3")
    parts = [f"buvid3={b3}"]
    if b4:
        parts.append(f"buvid4={b4}")
    return "; ".join(parts)


def device_cookie() -> str:
    """返回可拼进 Cookie 头的设备指纹片段；取不到时返回空字符串。

    永不上抛：调用方在构建 yt-dlp 选项/请求头时调用，指纹只是缓解手段，
    失败必须降级到「不带指纹」，而不是让整个转写失败。
    """
    global _cached

    now = time.time()
    with _lock:
        if _cached is not None:
            value, expires_at = _cached
            if now < expires_at:
                return value or ""

    try:
        value = _fetch_once()
        ttl = _CACHE_TTL_SECONDS
    except Exception as exc:  # 网络异常 / 风控 / 格式变化都走这里
        logger.warning("获取 B站设备指纹失败，本次不带 buvid：%s", exc)
        value = ""
        ttl = _FAILURE_COOLDOWN_SECONDS

    with _lock:
        _cached = (value, now + ttl)
    return value


def reset_cache() -> None:
    """清空缓存（测试与运维用）。"""
    global _cached
    with _lock:
        _cached = None
