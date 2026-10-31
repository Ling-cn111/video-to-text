"""B站分享短链（b23.tv）展开 + 与解析链路的接线。

## 背景（真机反馈）

手机端从 B站 App 点「分享 → 视频转文字」后，App 打开就报
「视频不存在或链接有误」。排查下来链接本身没错：分享给的是
`https://b23.tv/AbCdEfG`，而这个短链末段不是 BV 号，前端 `canonicalUrlFor`
认不出来，于是把短链原样送后端；后端 view API 与字幕/playurl 两处快路径
都只认 `bilibili.com` 域名，全部落空，只能退回 yt-dlp 抓网页，而云端机房
IP 抓 B站网页恒定 412 → 归到 `unable to download webpage` → 那句误导提示。

## 本文件固定的契约

1. 只展开 `b23.tv`；其他链接原样返回（`expand_short_link` 直接 None）；
2. 跳转链路的**每一跳**都必须在白名单内，遇到第三方域名立刻放弃；
3. 展开结果必须落在 `/video/` 路径，否则视为失败；
4. 任何异常一律返回 None 交回原链接 —— 展开是优化路径，不能让用户看到报错；
5. `parse_video_url` 展开成功后用展开地址走 view API，**不抓网页**；
6. `_resolve_target_url` 也展开一次（任务可能被脚本直接创建，绕过了 `/api/parse`）。
"""

import httpx
import pytest

from app.services import parser, short_link


class _FakeResponse:
    def __init__(self, status: int, headers: dict | None = None):
        self.status_code = status
        self.headers = headers or {}


class _FakeStreamCtx:
    def __init__(self, response: _FakeResponse):
        self._response = response

    def __enter__(self):
        return self._response

    def __exit__(self, *exc):
        return False


class _FakeClient:
    """按预设的跳转序列回应；每次 `stream` 消费一条。"""

    def __init__(self, hops: list[tuple[int, str | None]]):
        self._hops = hops
        self._index = 0
        self.requested: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def stream(self, method: str, url: str):
        self.requested.append(url)
        if self._index >= len(self._hops):
            raise AssertionError(f"跳转次数超出预期：{url}")
        status, location = self._hops[self._index]
        self._index += 1
        headers = {"location": location} if location else {}
        return _FakeStreamCtx(_FakeResponse(status, headers))


@pytest.fixture(autouse=True)
def _no_fingerprint(monkeypatch):
    """短链展开会拼 Cookie；测试不联网取指纹。"""
    from app.services import ytdlp_options

    monkeypatch.setattr(ytdlp_options, "_fingerprint_cookie", lambda: "", raising=False)


def _patch_client(monkeypatch, hops: list[tuple[int, str | None]]) -> _FakeClient:
    client = _FakeClient(hops)

    def _factory(*args, **kwargs):
        return client

    monkeypatch.setattr(short_link.httpx, "Client", _factory)
    return client


# ---------------------------------------------------------------- 纯函数


def test_is_short_link_only_for_b23():
    assert short_link.is_short_link("https://b23.tv/BV1GJ411x7h7")
    assert short_link.is_short_link("  https://b23.tv/AbCdEfG  ")
    assert not short_link.is_short_link("https://www.bilibili.com/video/BV1GJ411x7h7")
    assert not short_link.is_short_link("https://example.com/video/BV1GJ411x7h7")
    assert not short_link.is_short_link("")


def test_expand_follows_redirect_to_video_page(monkeypatch):
    _patch_client(monkeypatch, [(302, "https://www.bilibili.com/video/BV1GJ411x7h7/"), (200, None)])

    result = short_link.expand_short_link("https://b23.tv/BV1GJ411x7h7")

    assert result == "https://www.bilibili.com/video/BV1GJ411x7h7/"


def test_expand_resolves_relative_location(monkeypatch):
    """短链服务偶尔回相对路径（`/video/BVxxx`），必须补成绝对地址再跟随。

    不补的话 `client.stream("GET", "/video/BVxxx")` 会直接抛 httpx 的
    「相对 URL 不合法」，整个展开降级成 None —— 用户还是报「视频不存在」。
    这里断言的是「相对路径被正确拼接」，而不是它必须跳到 bilibili.com：
    有些短链服务确实用同域路径重写。
    """
    _patch_client(monkeypatch, [(302, "/video/BV1GJ411x7h7"), (200, None)])

    result = short_link.expand_short_link("https://b23.tv/a")

    assert result == "https://b23.tv/video/BV1GJ411x7h7"


def test_expand_rejects_third_party_hop(monkeypatch):
    """短链被劫持后指向第三方域名时必须放弃，不能把请求发出去。"""
    _patch_client(monkeypatch, [(302, "https://evil.example.com/video/BV1GJ411x7h7")])

    assert short_link.expand_short_link("https://b23.tv/AbCdEfG") is None


def test_expand_rejects_non_video_target(monkeypatch):
    """展开到 B站 首页 / 活动页没有意义，当作失败交回原链接。"""
    _patch_client(monkeypatch, [(302, "https://www.bilibili.com/"), (200, None)])

    assert short_link.expand_short_link("https://b23.tv/AbCdEfG") is None


def test_expand_returns_none_on_network_error(monkeypatch):
    def _boom(*args, **kwargs):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(short_link.httpx, "Client", _boom)

    assert short_link.expand_short_link("https://b23.tv/AbCdEfG") is None


def test_expand_ignores_non_short_link(monkeypatch):
    """非短链不该发任何请求。"""

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("非短链不该发起请求")

    monkeypatch.setattr(short_link.httpx, "Client", _should_not_be_called)

    assert short_link.expand_short_link("https://www.bilibili.com/video/BV1GJ411x7h7") is None


# ---------------------------------------------------------------- 与解析链路接线


class _FakeYDL:
    called = False

    def __init__(self, options):
        _FakeYDL.called = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        raise AssertionError(f"短链展开后应走 view API，不该抓网页：{url}")


def test_parse_expands_short_link_then_uses_view_api(monkeypatch):
    """展开成功后必须用展开地址走 view API —— 这是「视频不存在」的根治点。"""
    from app.services import bili_playurl

    seen: list[str] = []

    def _get_video_meta(bvid: str):
        seen.append(bvid)
        return bili_playurl.VideoMeta(
            bvid=bvid,
            aid=1,
            cid=2,
            title="短链视频",
            cover="https://i0.hdslb.com/a.jpg",
            duration=100,
        )

    monkeypatch.setattr(bili_playurl, "get_video_meta", _get_video_meta)
    monkeypatch.setattr(parser.yt_dlp, "YoutubeDL", _FakeYDL)
    _FakeYDL.called = False
    _patch_client(monkeypatch, [(302, "https://www.bilibili.com/video/BV1GJ411x7h7/"), (200, None)])

    video = parser.parse_video_url("https://b23.tv/BV1GJ411x7h7")

    assert seen == ["BV1GJ411x7h7"]
    assert video.videoId == "BV1GJ411x7h7"
    assert video.title == "短链视频"
    assert _FakeYDL.called is False


def test_parse_falls_back_to_original_when_expand_fails(monkeypatch):
    """展开失败不能挡住解析：原链接照旧交给 yt-dlp。"""
    captured: list[str] = []

    class _WorkingYDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            captured.append(url)
            return {"title": "兜底标题", "duration": 1, "id": "AbCdEfG"}

    monkeypatch.setattr(parser.yt_dlp, "YoutubeDL", _WorkingYDL)
    _patch_client(monkeypatch, [(500, None)])

    video = parser.parse_video_url("https://b23.tv/AbCdEfG")

    assert captured == ["https://b23.tv/AbCdEfG"]
    assert video.title == "兜底标题"


def test_resolve_target_url_expands_short_link(monkeypatch):
    """任务创建也可能绕过 `/api/parse`（脚本 / 重试 / 第三方），这里必须幂等展开。"""
    from app.routers import transcribe as transcribe_router
    from app.schemas import StartTranscribeRequest, VideoInfo

    _patch_client(monkeypatch, [(302, "https://www.bilibili.com/video/BV1GJ411x7h7/"), (200, None)])

    payload = StartTranscribeRequest(
        videoId="AbCdEfG",
        url="https://b23.tv/AbCdEfG",
        video=VideoInfo(
            videoId="AbCdEfG",
            platform="bilibili",
            title="t",
            cover="",
            duration=1,
        ),
    )

    assert transcribe_router._resolve_target_url(payload) == (
        "https://www.bilibili.com/video/BV1GJ411x7h7/"
    )


def test_resolve_target_url_rejects_unsupported_expansion(monkeypatch):
    """短链展开到非白名单域名时，白名单校验已经拦掉，最终仍是原短链 → 平台仍认得。"""
    from app.routers import transcribe as transcribe_router
    from app.schemas import StartTranscribeRequest, VideoInfo

    _patch_client(monkeypatch, [(302, "https://evil.example.com/video/BV1GJ411x7h7")])

    payload = StartTranscribeRequest(
        videoId="AbCdEfG",
        url="https://b23.tv/AbCdEfG",
        video=VideoInfo(
            videoId="AbCdEfG",
            platform="bilibili",
            title="t",
            cover="",
            duration=1,
        ),
    )

    assert transcribe_router._resolve_target_url(payload) == "https://b23.tv/AbCdEfG"


@pytest.mark.network
def test_expand_real_short_link():
    """真实短链（B站 官方分享页的稳定示例）。CI 中跳过。"""
    result = short_link.expand_short_link("https://b23.tv/BV1GJ411x7h7")

    assert result is not None
    assert "bilibili.com" in result
    assert "/video/" in result
