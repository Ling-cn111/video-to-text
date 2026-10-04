"""B站字幕快路径：CC 字幕 / AI 字幕存在时直接解析，秒级返回文字稿。

现实约束（阶段一）：B站 AI 字幕的播放器接口通常需要登录 Cookie 才暴露，
未登录状态下 yt-dlp 仅能拿到 UP 主上传的 CC 字幕。无字幕时返回 None，
由上层回退到「下载音频 + ASR」路径。接入 Cookie 支持（BILI_COOKIE env）已列入后续计划。
"""
import json
from dataclasses import dataclass

import httpx
import yt_dlp

from app.url_guard import is_blocked_host

# 字幕下载 URL 仅允许 B站自家域名（该 URL 来自 yt-dlp 对白名单平台的解析结果）
_ALLOWED_SUBTITLE_HOST_SUFFIXES = ("bilibili.com", "hdslb.com", "bilibili.tv")

# 语言优先级：CC 中文字幕 > AI 中文字幕 > 任意中文 > 英文
_PREFERRED_LANG_KEYWORDS = ("zh",)


@dataclass(frozen=True)
class SubtitleTrack:
    lang: str
    url: str
    ext: str
    from_cc: bool


def _is_allowed_subtitle_url(url: str) -> bool:
    from urllib.parse import urlparse

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


def try_extract_subtitle_transcript(url: str, timeout_seconds: int = 20) -> list[dict] | None:
    """尝试从字幕快路径拿到 [{time, text}]；无可用字幕返回 None。"""
    options = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    tracks = list_subtitle_tracks(info or {})
    if not tracks:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.bilibili.com/",
    }
    with httpx.Client(timeout=timeout_seconds, headers=headers, follow_redirects=True) as client:
        for track in tracks[:5]:  # 最多尝试前 5 条轨道
            if not _is_allowed_subtitle_url(track.url):
                continue
            try:
                response = client.get(track.url)
                response.raise_for_status()
                items = parse_subtitle_body(response.text, track.ext)
            except Exception:
                continue
            if items:
                return items
    return None
