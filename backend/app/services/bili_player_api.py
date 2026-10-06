"""B站弹幕元数据接口字幕抓取（未登录，零 Cookie）。

链路（侦察报告 backend/docs/bili-subtitle-recon.md，任务 G）：
  GET /x/web-interface/view?bvid=      → data.aid / data.cid / title
  GET /x/v2/dm/view?aid=&oid=&type=1   → data.subtitle.subtitles[]（AI 字幕轨道）
  轨道 subtitle_url（aisubtitle.hdslb.com，https 可直连）→ 下载与解析由 subtitles.py 完成

关键事实（2026-10-06 实测）：
- dm/view 未登录即可访问，无需 Cookie / wbi 签名；player/v2 与 wbi/v2 的未登录响应恒为空轨道
- AI 轨道（lan 以 "ai-" 开头）仅在 ai_status == 2（已生成）时可用，0/1（未生成/生成中）跳过；
  UP 主手传 CC 轨道无 ai_status 过滤问题
- 字幕 URL 仅允许 B站 CDN 域名（下载侧由 subtitles.is_allowed_subtitle_url 再校验一次）
- 请求间隔 REQUEST_INTERVAL 秒，模拟正常网页节奏，避免触发反爬
"""
import logging
import threading
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

REQUEST_INTERVAL = 1.5

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
}

_API_BASE = "https://api.bilibili.com"

_throttle_lock = threading.Lock()
_last_request_at = 0.0


def _throttle() -> None:
    """跨线程请求节流：距上次请求不足 REQUEST_INTERVAL 则等待。"""
    global _last_request_at
    with _throttle_lock:
        elapsed = time.monotonic() - _last_request_at
        if elapsed < REQUEST_INTERVAL:
            time.sleep(REQUEST_INTERVAL - elapsed)
        _last_request_at = time.monotonic()


@dataclass(frozen=True)
class BiliSubtitleTrack:
    """dm/view 返回的字母轨道（lang/URL 已规范化）。"""

    lan: str
    subtitle_url: str  # 已升级为 https
    is_ai: bool


def _get_json(client: httpx.Client, url: str) -> dict | None:
    _throttle()
    try:
        response = client.get(url, headers=_HEADERS, timeout=20)
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def get_video_info(bvid: str) -> dict | None:
    """view API：bvid → { aid, cid, title }；失败返回 None。"""
    with httpx.Client(follow_redirects=True) as client:
        payload = _get_json(client, f"{_API_BASE}/x/web-interface/view?bvid={bvid}")
    if not payload or payload.get("code") != 0:
        logger.warning("bili view API 未取到视频信息（bvid=%s）", bvid)
        return None
    data = payload.get("data") or {}
    aid, cid = data.get("aid"), data.get("cid")
    if not aid or not cid:
        return None
    return {"aid": aid, "cid": cid, "title": str(data.get("title") or "")}


def get_subtitle_tracks(bvid: str, cid: int | None = None) -> list[BiliSubtitleTrack]:
    """dm/view：视频 → 字幕轨道列表（中文优先，AI 轨道仅收 ai_status=2）；失败返回空列表。

    dm/view 以 aid 为准（cid 仅在调用方已持有时透传，省一次 view 请求）；
    aid 未知时内部查 view API（结果按 bvid 缓存）。不抛异常——调用方（字幕快路径
    分层降级）依赖「空列表 = 本层无字幕」继续降级。
    """
    try:
        aid = _resolve_aid(bvid)
        if not aid:
            return []
        if cid is None:
            cid = _CID_CACHE.get(bvid)
            if not cid:
                return []
        with httpx.Client(follow_redirects=True) as client:
            payload = _get_json(client, f"{_API_BASE}/x/v2/dm/view?aid={aid}&oid={cid}&type=1")
    except Exception:
        logger.warning("bili dm/view 字幕探测失败（bvid=%s）", bvid, exc_info=True)
        return []

    if not payload or payload.get("code") != 0:
        logger.info("bili dm/view 无字幕数据（bvid=%s code=%s）", bvid, payload.get("code") if payload else None)
        return []
    subtitles = ((payload.get("data") or {}).get("subtitle") or {}).get("subtitles") or []

    tracks: list[BiliSubtitleTrack] = []
    for sub in subtitles:
        lan = str(sub.get("lan") or "")
        raw_url = str(sub.get("subtitle_url") or "")
        if not lan or not raw_url:
            continue
        is_ai = lan.startswith("ai-")
        # AI 轨道 ai_status: 2=已生成；0/1（未生成/生成中）跳过。CC 轨道不适用该字段。
        if is_ai and sub.get("ai_status") != 2:
            continue
        tracks.append(BiliSubtitleTrack(lan=lan, subtitle_url=_to_https(raw_url), is_ai=is_ai))

    def rank(track: BiliSubtitleTrack) -> tuple:
        lang = track.lan.lower()
        zh_score = 2 if lang.startswith("zh") else (1 if "zh" in lang else 0)
        return (0 if not track.is_ai else 1, -zh_score)  # CC 优先于 AI；中文优先

    tracks.sort(key=rank)
    logger.info("bili dm/view 命中 %d 条字幕轨道（bvid=%s）", len(tracks), bvid)
    return tracks


_AID_CACHE: dict[str, int] = {}
_CID_CACHE: dict[str, int] = {}


def _resolve_aid(bvid: str) -> int:
    """bvid → aid（dm/view 参数体系）；带缓存，同任务内不重复请求 view API。"""
    cached = _AID_CACHE.get(bvid)
    if cached:
        return cached
    info = get_video_info(bvid)
    if not info:
        return 0
    _AID_CACHE[bvid] = int(info["aid"])
    _CID_CACHE[bvid] = int(info["cid"])
    return _AID_CACHE[bvid]


def _to_https(url: str) -> str:
    return url.replace("http://", "https://", 1) if url.startswith("http://") else url
