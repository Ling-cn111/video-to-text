"""B站播放流地址（playurl API）——绕过网页被抓的原因。

## 为什么需要这个模块

云端实测（阿里云 cn-hangzhou 出口）：

    hardened view_api  = 200   ← 带设备指纹后通了
    hardened video_page = 412  ← 即使带指纹 + 首页会话 Cookie 也是 412（3400B 风控页）

yt-dlp 抓 B站视频要**解析网页 html**，所以在这台机器上必然 412。后果是：
有 CC 字幕的视频能靠字幕快路径完成，但**没有 CC 字幕的视频（需要下载音频走
ASR）一定会失败**。

而 JSON API 是通的。因此这里直接用官方 playurl 接口拿 DASH 音频流地址，
完全不碰网页——把「解析」和「下载」两件事解耦。

## 接口选择

    GET /x/player/playurl?bvid=&cid=&fnval=16&fnver=0&fourk=1

- **免 wbi 签名**：wbi 版（`/x/player/wbi/playurl`）需要额外算签名，实测免签版
  在未登录状态下同样返回完整 DASH，没必要引入签名依赖。
- `fnval=16` 才返回 `dash.audio[]`（`fnval=0` 只给 `durl` 整段 flv/mp4，
  体积大且含视频轨）。
- 音频按 `bandwidth` 挑最高档，通常是 30280（192K）。

## CDN 请求头的硬性要求（实测）

`baseUrl` 指向 `*.bilivideo.com`，**必须同时带 UA + Referer + Cookie**：

    无 Referer          → 403
  仅有 Referer         → 403
  UA + Referer + Cookie → 200

少任何一个都是 403 且返回 text/html，因此下载侧要校验 content-type，
避免把错误页当音频喂给 ASR。
"""
import logging
from dataclasses import dataclass

import httpx

from app import config
from app.services import ytdlp_options

logger = logging.getLogger(__name__)

VIEW_API = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_API = "https://api.bilibili.com/x/player/playurl"

# 超时：playurl 只是拿地址，很快；给足余量但不要挂死
_API_TIMEOUT = 20.0


@dataclass(frozen=True)
class AudioStream:
    """一条可下载的音频流。"""

    url: str
    backup_urls: tuple[str, ...]
    mime_type: str
    bandwidth: int


def _headers() -> dict[str, str]:
    """API 与 CDN 共用的一套请求头（CDN 缺任何一个都 403）。"""
    headers = {
        "User-Agent": config.BILI_USER_AGENT
        or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com/",
        "Accept": "application/json, text/plain, */*",
    }
    cookie = ytdlp_options.cookie_header()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _get_json(client: httpx.Client, url: str) -> dict:
    response = client.get(url, headers=_headers(), timeout=_API_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise ValueError(f"B站接口返回 code={payload.get('code')} {payload.get('message') or ''}")
    return payload.get("data") or {}


@dataclass(frozen=True)
class VideoMeta:
    """view API 返回的元数据（`/api/parse` 与 playurl 共用）。"""

    bvid: str
    aid: int
    cid: int
    title: str
    cover: str
    duration: int


def get_video_meta(bvid: str) -> VideoMeta:
    """bvid → 元数据。

    这是 `/x/web-interface/view` 的纯 JSON 封装：机房 IP 上网页 412，但这个接口
    带设备指纹正常（见 §7.3.2 的 baseline/hardened 对照）。`/api/parse` 走它，
    就不必让 yt-dlp 去抓网页。
    """
    with httpx.Client(follow_redirects=True) as client:
        data = _get_json(client, f"{VIEW_API}?bvid={bvid}")

    cid = data.get("cid")
    if not cid:
        raise ValueError(f"view API 未返回 cid（bvid={bvid}）")

    cover = str(data.get("pic") or "")
    # B站 CDN 支持 https：统一升级，避免 https 站点引用 http 图片被混合内容策略拦截
    if cover.startswith("http://"):
        cover = f"https://{cover[len('http://'):]}"

    return VideoMeta(
        bvid=str(data.get("bvid") or bvid),
        aid=int(data.get("aid") or 0),
        cid=int(cid),
        title=str(data.get("title") or "").strip(),
        cover=cover,
        duration=int(data.get("duration") or 0),
    )


def resolve_cid(bvid: str) -> int:
    """bvid → cid（playurl 必需）。"""
    return get_video_meta(bvid).cid


def get_audio_stream(bvid: str, cid: int | None = None) -> AudioStream:
    """取码率最高的 DASH 音频流。

    失败一律抛 ValueError（调用方据此回退 yt-dlp），不吞异常——
    「为什么没走 API」必须能在日志里看到。
    """
    if cid is None:
        cid = resolve_cid(bvid)

    with httpx.Client(follow_redirects=True) as client:
        data = _get_json(
            client,
            f"{PLAYURL_API}?bvid={bvid}&cid={cid}&fnval=16&fnver=0&fourk=1",
        )

    dash = data.get("dash") or {}
    audios = dash.get("audio") or []
    if not audios:
        raise ValueError(f"playurl 未返回 DASH 音频轨（bvid={bvid} cid={cid}）")

    best = max(audios, key=lambda item: item.get("bandwidth") or 0)
    url = best.get("baseUrl") or best.get("base_url")
    if not url:
        raise ValueError("playurl 音频轨缺少 baseUrl")

    backups = tuple(best.get("backupUrl") or best.get("backup_url") or ())
    return AudioStream(
        url=str(url),
        backup_urls=backups,
        mime_type=str(best.get("mimeType") or best.get("mime_type") or ""),
        bandwidth=int(best.get("bandwidth") or 0),
    )
