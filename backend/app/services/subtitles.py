"""B站字幕快路径：多层级降级探测，命中即秒级返回文字稿。

降级链（任务 G，侦察报告 backend/docs/bili-subtitle-recon.md）：
  1. yt-dlp 未登录拿 UP 主手传 CC 字幕（info.subtitles，零 Cookie）
  2. B站弹幕元数据接口 /x/v2/dm/view 的 AI 字幕（bili_player_api，零 Cookie 零签名）
  3. （预留）yt-dlp + BILI_COOKIE 的 AI 字幕兜底层，实现见任务 F 计划
  4. 全部未命中 → 返回 None，上层走「下载音频 + ASR」

每层失败静默降级；命中层通过返回值 (items, source) 告知调用方写入 transcriptSource。
日志只记录命中层与条数，不记录字幕 URL（含时效 auth_key）。
"""
import json
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import yt_dlp

from app.url_guard import is_blocked_host

logger = logging.getLogger(__name__)

# 字幕下载 URL 仅允许 B站自家域名（该 URL 来自 yt-dlp / dm-view 对白名单平台的解析结果）
_ALLOWED_SUBTITLE_HOST_SUFFIXES = ("bilibili.com", "hdslb.com", "bilibili.tv")

# 语言优先级：CC 中文字幕 > AI 中文字幕 > 任意中文 > 英文
_PREFERRED_LANG_KEYWORDS = ("zh",)


@dataclass(frozen=True)
class SubtitleTrack:
    lang: str
    url: str
    ext: str
    from_cc: bool


def is_allowed_subtitle_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if is_blocked_host(host):
        return False
    return any(host == s or host.endswith(f".{s}") for s in _ALLOWED_SUBTITLE_HOST_SUFFIXES)


def list_subtitle_tracks(info: dict) -> list[SubtitleTrack]:
    """从 yt-dlp 的 info 中整理字幕轨道：CC 优先于 AI，中文优先。"""
    tracks: list[SubtitleTrack] = []
    for lang, fmts in (info.get("subtitles") or {}).items():
        for fmt in fmts or []:
            if fmt.get("url"):
                tracks.append(
                    SubtitleTrack(lang=lang, url=fmt["url"], ext=fmt.get("ext", ""), from_cc=True)
                )
    for lang, fmts in (info.get("automatic_captions") or {}).items():
        for fmt in fmts or []:
            if fmt.get("url"):
                tracks.append(
                    SubtitleTrack(lang=lang, url=fmt["url"], ext=fmt.get("ext", ""), from_cc=False)
                )

    def rank(track: SubtitleTrack) -> tuple:
        lang = track.lang.lower()
        zh_score = 2 if lang.startswith("zh") else (1 if "zh" in lang else 0)
        return (
            0 if track.from_cc else 1,
            -zh_score,
            0 if lang.startswith("zh") else 1,
            0 if track.ext in ("json", "json3") else 1,
        )

    tracks.sort(key=rank)
    return tracks


def _parse_bilibili_json(text: str) -> list[dict]:
    """B站 CC 字幕格式：{"body": [{"from": 1.2, "to": 3.4, "content": "..."}]}"""
    body = json.loads(text).get("body") or []
    return [{"time": float(item.get("from", 0)), "text": str(item.get("content", "")).strip()} for item in body]


def _parse_json3(text: str) -> list[dict]:
    """YouTube json3 风格：{"events": [{"tStartMs": ..., "segs": [{"utf8": ...}]}]}"""
    events = json.loads(text).get("events") or []
    items = []
    for event in events:
        start = event.get("tStartMs")
        if start is None:
            continue
        text = "".join(seg.get("utf8", "") for seg in (event.get("segs") or [])).strip()
        if text:
            items.append({"time": start / 1000, "text": text})
    return items


def _parse_vtt(text: str) -> list[dict]:
    """WebVTT/SRT：00:00:01.000 --> 00:00:03.000 两行式。"""
    import re

    timestamp = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})")
    items: list[dict] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = timestamp.search(line)
        if not match:
            continue
        h, m, s, ms = (int(match.group(i)) for i in range(1, 5))
        time = h * 3600 + m * 60 + s + ms / 1000
        content_lines: list[str] = []
        for following in lines[index + 1 : index + 3]:
            if "-->" in following or not following.strip():
                break
            content_lines.append(following.strip())
        text = " ".join(content_lines).strip()
        if text:
            items.append({"time": time, "text": text})
    return items


def parse_subtitle_body(text: str, ext: str) -> list[dict] | None:
    """按扩展名解析字幕体；解析不出任何条目返回 None。"""
    parsers = []
    if ext in ("json", "json3"):
        parsers = [_parse_bilibili_json, _parse_json3]
    elif ext == "vtt":
        parsers = [_parse_vtt]
    elif ext == "srt":
        parsers = [_parse_vtt]
    else:
        parsers = [_parse_bilibili_json, _parse_json3, _parse_vtt]
    for parser in parsers:
        try:
            items = parser(text)
        except Exception:
            continue
        if items:
            return items
    return None


def try_extract_subtitle_transcript(
    url: str, timeout_seconds: int = 20
) -> tuple[list[dict], str] | None:
    """分层字幕探测：命中返回 (items, source)，全部未命中返回 None。

    source: 'subtitle_cc'（yt-dlp CC）| 'subtitle_ai'（dm/view AI 字幕）。
    任何一层失败静默降级，不抛异常、不阻断转写主流程。
    """
    # 层 1：yt-dlp 未登录拿 UP 主手传 CC 字幕
    try:
        options = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        cc_tracks = [t for t in list_subtitle_tracks(info or {}) if t.from_cc]
        for track in cc_tracks[:5]:
            items = _download_track_items(track.url, track.ext, timeout_seconds)
            if items:
                logger.info("字幕快路径命中 layer=yt-dlp-CC 条数=%d", len(items))
                return items, "subtitle_cc"
    except Exception:
        logger.warning("yt-dlp CC 字幕层失败，降级 dm/view", exc_info=True)

    # 层 2：弹幕元数据接口的 AI 字幕（未登录可用，无需 Cookie/签名）
    try:
        from app.services import bili_player_api

        bvid = _extract_bvid(url)
        if bvid:
            for track in bili_player_api.get_subtitle_tracks(bvid)[:5]:
                items = _download_track_items(track.subtitle_url, "json", timeout_seconds)
                if items:
                    source = "subtitle_ai" if track.is_ai else "subtitle_cc"
                    logger.info("字幕快路径命中 layer=dm-view lan=%s 条数=%d", track.lan, len(items))
                    return items, source
    except Exception:
        logger.warning("dm/view AI 字幕层失败", exc_info=True)

    # 层 3（预留）：yt-dlp + BILI_COOKIE 的 AI 字幕兜底层——任务 F 计划，
    # 依赖 config.BILI_COOKIE 透传 cookiefile；dm/view 覆盖不到位时再实现。
    return None


def _extract_bvid(url: str) -> str | None:
    """从 B站视频页 URL 提取 BV 号；非 B站链接返回 None。"""
    host = (urlparse(url).hostname or "").lower()
    if not (host == "bilibili.com" or host.endswith(".bilibili.com")):
        return None
    import re

    match = re.search(r"/video/(BV[0-9A-Za-z]+)", url)
    return match.group(1) if match else None


def _download_track_items(track_url: str, ext: str, timeout_seconds: int) -> list[dict] | None:
    """白名单校验 → https 升级 → 下载 → 解析；任何一步失败返回 None。"""
    if not is_allowed_subtitle_url(track_url):
        return None
    download_url = track_url.replace("http://", "https://", 1) if track_url.startswith("http://") else track_url
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.bilibili.com/",
    }
    try:
        with httpx.Client(timeout=timeout_seconds, headers=headers, follow_redirects=True) as client:
            response = client.get(download_url)
            response.raise_for_status()
        return parse_subtitle_body(response.text, ext)
    except Exception:
        return None
