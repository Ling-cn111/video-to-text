"""`parser` 的 API 优先路径（B站 view API 取元数据）。

## 背景

云端实测（阿里云 cn-hangzhou 出口）：

    hardened view_api   = 200   带设备指纹后通了
    hardened video_page = 412   即使带指纹 + 首页会话 Cookie 仍 412（3400B 风控页）

`/api/parse` 前端第一步就调用，而 yt-dlp 解析元数据必须先抓网页 → 机房 IP 必失败。
音频侧已经改走 playurl API（见 test_bili_playurl.py），但 parse 没改的话，
**前端根本没机会走到转写**：用户按下「解析」就是一个 500。

## 本文件固定的契约

1. B站链接优先走 view API，完全不碰网页，也不调用 yt-dlp；
2. 封面 `http://` 自动升级为 `https://`（否则 https 站点会被混合内容策略拦）；
3. API 任何失败都静默回退 yt-dlp —— 优化路径不能降低可用性；
4. 非 B站 / 非 BV 号（av 号、番剧 ep）直接交给 yt-dlp，不发无谓请求。
"""

import pytest

from app.services import parser


@pytest.fixture(autouse=True)
def _no_network_fingerprint(monkeypatch):
    """不联网取指纹；测试只关心「走没走 API」，不关心 Cookie 值。"""
    from app.services import ytdlp_options

    monkeypatch.setattr(ytdlp_options, "_fingerprint_cookie", lambda: "", raising=False)


class _FakeYDL:
    """yt-dlp 替身：默认断言「不该被调用」。"""

    called = False

    def __init__(self, options):
        _FakeYDL.called = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        raise AssertionError(f"B站链接应走 view API，不该调用 yt-dlp：{url}")


def _stub_meta(monkeypatch, meta=None, exc: Exception | None = None):
    from app.services import bili_playurl

    def _get(bvid):
        if exc is not None:
            raise exc
        return meta

    monkeypatch.setattr(bili_playurl, "get_video_meta", _get)
    monkeypatch.setattr(parser.yt_dlp, "YoutubeDL", _FakeYDL)


def _meta(**overrides):
    from app.services.bili_playurl import VideoMeta

    base = dict(
        bvid="BV1BE411K7h9",
        aid=95371935,
        cid=162820339,
        title="鲨鱼大战鳄鱼——Maneater游戏实况",
        cover="https://i2.hdslb.com/bfs/archive/x.jpg",
        duration=203,
    )
    base.update(overrides)
    return VideoMeta(**base)


def test_bilibili_url_uses_view_api_not_ytdlp(monkeypatch):
    _FakeYDL.called = False
    _stub_meta(monkeypatch, _meta())

    video = parser.parse_video_url("https://www.bilibili.com/video/BV1BE411K7h9")

    assert video.title == "鲨鱼大战鳄鱼——Maneater游戏实况"
    assert video.duration == 203
    assert video.platform == "bilibili"
    assert video.videoId == "BV1BE411K7h9"
    assert _FakeYDL.called is False, "有 API 路径时不该抓网页"


def test_cover_upgraded_to_https(monkeypatch):
    """B站返回 http 封面；不升级会被 https 站点的混合内容策略拦掉。"""
    _stub_meta(monkeypatch, _meta(cover="https://i0.hdslb.com/a.jpg"))

    video = parser.parse_video_url("https://www.bilibili.com/video/BV1BE411K7h9")
    assert video.cover.startswith("https://")


def test_api_failure_falls_back_to_ytdlp(monkeypatch):
    """API 挂了不能让解析整体失败——回退 yt-dlp 至少在其他网络环境可用。"""
    _stub_meta(monkeypatch, exc=ValueError("412"))

    class _WorkingYDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            return {
                "title": "yt-dlp 标题",
                "thumbnail": "http://i2.hdslb.com/cover.jpg",
                "duration": 100,
                "id": "BV1BE411K7h9",
            }

    monkeypatch.setattr(parser.yt_dlp, "YoutubeDL", _WorkingYDL)

    video = parser.parse_video_url("https://www.bilibili.com/video/BV1BE411K7h9")

    assert video.title == "yt-dlp 标题"
    assert video.cover.startswith("https://"), "回退路径也要升级 https"


def test_api_returning_empty_title_falls_back(monkeypatch):
    """API 返回空标题视为无效，不能把空标题当成功返回给前端。"""
    _stub_meta(monkeypatch, _meta(title=""))

    class _WorkingYDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            return {"title": "兜底标题", "duration": 1, "id": "BV1BE411K7h9"}

    monkeypatch.setattr(parser.yt_dlp, "YoutubeDL", _WorkingYDL)

    video = parser.parse_video_url("https://www.bilibili.com/video/BV1BE411K7h9")
    assert video.title == "兜底标题"


def test_av_url_skips_api(monkeypatch):
    """av 号走不了 view API 的 bvid 参数，应直接交给 yt-dlp。"""
    from app.services import bili_playurl

    monkeypatch.setattr(
        bili_playurl, "get_video_meta", lambda *a, **kw: pytest.fail("av 号不该走 API")
    )

    class _WorkingYDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            return {"title": "av 视频", "duration": 5, "id": "av123"}

    monkeypatch.setattr(parser.yt_dlp, "YoutubeDL", _WorkingYDL)

    video = parser.parse_video_url("https://www.bilibili.com/video/av123")
    assert video.title == "av 视频"


def test_bangumi_url_skips_api(monkeypatch):
    """番剧 ep 没有 BV 号，不能拿去拼 bvid 参数。"""
    from app.services import bili_playurl

    monkeypatch.setattr(
        bili_playurl, "get_video_meta", lambda *a, **kw: pytest.fail("番剧不该走 API")
    )

    class _WorkingYDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            return {"title": "番剧", "duration": 5, "id": "ep123"}

    monkeypatch.setattr(parser.yt_dlp, "YoutubeDL", _WorkingYDL)

    video = parser.parse_video_url("https://www.bilibili.com/bangumi/play/ep123456")
    assert video.title == "番剧"
