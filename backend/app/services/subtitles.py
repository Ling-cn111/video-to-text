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
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import yt_dlp

from app.services.ytdlp_options import base_options as _base_options
from app.url_guard import is_blocked_host

logger = logging.getLogger(__name__)

# 字幕下载 URL 仅允许 B站自家域名（该 URL 来自 yt-dlp / dm-view 对白名单平台的解析结果）
_ALLOWED_SUBTITLE_HOST_SUFFIXES = ("bilibili.com", "hdslb.com", "bilibili.tv")

# 语言优先级：CC 中文字幕 > AI 中文字幕 > 任意中文 > 英文
_PREFERRED_LANG_KEYWORDS = ("zh",)

# ---- 字幕分句（punctuate_subtitle_items）----
# B站 AI 字幕按「口播节奏」切条，平均 11 字一条且不含任何标点（实测 171 条 / 171 条无句末标点）。
# 直接喂给前端会让 src/lib/article.ts 的 SENTENCE_END 永不命中，分段回退失效，
# 整篇退化成 500 字一堵的硬切长墙——这才是「字幕稿读不下去」的真正原因，不是缺标点本身。
# 因此按「时间间隔 + 累计字数」把碎片合并成句子，再补句末标点。
_SUBTITLE_GAP_SECONDS = 1.2  # 停顿超过该值视为句子边界（实测 gap p25=1.2s / p50=1.87s）
_SUBTITLE_MIN_CHARS = 24  # 但累计不足该字数时继续吞下一条，避免「短的发布会。」这类碎句
_SUBTITLE_MAX_CHARS = 60  # 上限：超过即强制断句，防止一段无声长句无界膨胀

# 真正的句子边界：命中即断句，不再向后合并
_SENTENCE_END_PUNCT = "。！？!?…"
# 软标点：不是句子边界，补句号前先剥掉，避免出现「，。」这种拼接痕迹
_SOFT_END_PUNCT = "，,、；;：:"
# 句末标点后可能跟随的收尾符号，判断「已结束」时要一起跳过
_CLOSERS = "”\"』」)）】》〉"
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")


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


def _ends_sentence(text: str) -> bool:
    """文本是否已以句子终结符收尾（跳过尾部的引号/括号等收尾符号）。"""
    stripped = text.rstrip().rstrip(_CLOSERS).rstrip()
    return bool(stripped) and stripped[-1] in _SENTENCE_END_PUNCT


def _add_terminator(text: str) -> str:
    """给一句补句末标点：先剥掉软标点（逗号顿号分号），再按语种补「。」或「.」。

    收尾符号（引号/括号）的位置按中文习惯处理：句号放在收尾引号**外面**，
    即「他说“好”。」而不是「他说“好。”」。
    """
    body = text.rstrip().rstrip(_CLOSERS).rstrip()
    closers = text[len(body):]  # 被剥掉的收尾符号要原样接回去
    while body and body[-1] in _SOFT_END_PUNCT:
        body = body[:-1].rstrip()
    if not body:
        return text
    if _ends_sentence(body):
        return body + closers
    terminator = "。" if _HAS_CJK.search(body) else "."
    return body + closers + terminator


def punctuate_subtitle_items(
    items: list[dict],
    gap_seconds: float = _SUBTITLE_GAP_SECONDS,
    min_chars: int = _SUBTITLE_MIN_CHARS,
    max_chars: int = _SUBTITLE_MAX_CHARS,
) -> list[dict]:
    """把碎片化的字幕条目合并成带句末标点的句子（不改时间戳语义、不引入新时间）。

    B站 AI 字幕一条 5-15 字、无任何标点。这里按「停顿 gap + 累计字数」把若干条
    合并成一句，句首时间取合并段里第一条的时间（即这句话开口的时刻），
    保证时间戳定位仍然准确。

    为什么不复用 asr.postprocess_items：
    1. 它逐条补标点，不合并 —— 结果是「短的发布会。多恩的发布会会唠这一场。」，
       11 字一个句号，读起来像电报，比不补更碎（实测 13 段 / 每段 14 个句号）；
    2. 它还会做繁简转换与重复词折叠，对 UP 主手传的 CC 字幕属额外加工；
    3. 它的 _SENTENCE_END 把逗号顿号分号也算句末，遇到「，」就跳过不补句号。

    参数取值依据（线上 171 条真实 AI 字幕实测）：gap=1.2s / min=24 / max=60 时
    合并出 58 句，经前端 article.ts 分段得到 12 段（每段 160-190 字），
    段落开头「…最后精神家园。库克复刻复刻因酷客更爱酷。逐光前行…」已接近正常文章。
    """
    merged: list[dict] = []
    buffer: list[str] = []
    start_time: float | None = None

    def flush() -> None:
        nonlocal buffer, start_time
        if buffer and start_time is not None:
            merged.append({"time": start_time, "text": _add_terminator("".join(buffer))})
        buffer = []
        start_time = None

    for index, item in enumerate(items):
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        if not buffer:
            start_time = float(item.get("time", 0.0))
        buffer.append(text)

        joined = "".join(buffer)
        # 已出现真正的句子终结符：立刻断句，不再吞下一条
        if _ends_sentence(joined):
            flush()
            continue

        next_item = items[index + 1] if index + 1 < len(items) else None
        if next_item is None:
            continue  # 最后一条留给循环结束后的 flush
        next_time = float(next_item.get("time", 0.0))
        current_time = float(item.get("time", 0.0))
        gap = next_time - current_time

        too_long = len(joined) >= max_chars
        long_pause = gap >= gap_seconds and len(joined) >= min_chars
        if too_long or long_pause:
            flush()

    flush()
    return merged


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
        options = {
            # 公共项见 ytdlp_options.py
            **_base_options(),
            "skip_download": True,
        }
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
