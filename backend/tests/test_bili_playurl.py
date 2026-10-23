"""`bili_playurl` 与 `audio.download_audio` 的 API 优先路径。

## 背景

云端实测（阿里云 cn-hangzhou 出口）：

    hardened view_api   = 200   （带设备指纹后通了）
    hardened video_page = 412   （即使带指纹 + 首页会话 Cookie 也无效，3400B 风控页）

yt-dlp 解析 B站必须先抓网页 html，所以在这台机器上必然 412。结果是有 CC 字幕
的视频能用字幕快路径完成，**没有 CC 字幕的视频（需要音频走 ASR）一定失败**。

解法：用官方 `playurl` JSON 接口直接拿 DASH 音频直链，完全不碰网页。

## 本文件固定的契约

1. 只对 B站链接走 API，其他平台/非视频 URL 立刻跳过（不发无谓请求）；
2. 挑 bandwidth 最高的音轨（默认 30280 = 192K）；
3. CDN 必须带 UA + Referer + Cookie，缺一个就是 403 + text/html，
   所以下载侧要校验 content-type，绝不能把错误页当音频；
4. 任一环节失败都回退 yt-dlp，不能让「优化路径」反而降低可用性。

所有用例都不联网：httpx 与 playurl 模块均被打桩。
"""
import io

import httpx
import pytest

from app.services import audio as audio_module
from app.services import bili_playurl


@pytest.fixture(autouse=True)
def _quiet_fingerprint(monkeypatch):
    """不联网取指纹；Cookie 固定为可断言的假值。"""
    from app.services import bili_fingerprint

    monkeypatch.setattr(bili_fingerprint, "device_cookie", lambda: "buvid3=TEST")


# ---- bili_playurl 解析 ----


def _client_returning(monkeypatch, payload_by_url: dict):
    """替换 httpx.Client，让 get() 按 URL 返回预置 JSON。"""

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, **kwargs):
            for key, payload in payload_by_url.items():
                if key in url:
                    return _Resp(payload)
            raise AssertionError(f"未预置的 URL：{url}")

    monkeypatch.setattr(bili_playurl.httpx, "Client", _Client)


def test_picks_highest_bandwidth_audio(monkeypatch):
    _client_returning(
        monkeypatch,
        {
            "playurl": {
                "code": 0,
                "data": {
                    "dash": {
                        "audio": [
                            {"id": 30216, "bandwidth": 65536, "baseUrl": "https://cdn/low.m4a"},
                            {"id": 30280, "bandwidth": 203786, "baseUrl": "https://cdn/high.m4a"},
                            {"id": 30232, "bandwidth": 130000, "baseUrl": "https://cdn/mid.m4a"},
                        ]
                    }
                },
            }
        },
    )

    stream = bili_playurl.get_audio_stream("BV1xx", cid=123)
    assert stream.url == "https://cdn/high.m4a"
    assert stream.bandwidth == 203786


def test_uses_camel_case_and_snake_case_keys(monkeypatch):
    """B站历史上两种命名都出现过，不能只认一种。"""
    _client_returning(
        monkeypatch,
        {
            "playurl": {
                "code": 0,
                "data": {
                    "dash": {
                        "audio": [
                            {
                                "id": 30280,
                                "bandwidth": 100,
                                "base_url": "https://cdn/snake.m4a",
                                "backup_url": ["https://cdn/b1.m4a"],
                            }
                        ]
                    }
                },
            }
        },
    )

    stream = bili_playurl.get_audio_stream("BV1xx", cid=123)
    assert stream.url == "https://cdn/snake.m4a"
    assert stream.backup_urls == ("https://cdn/b1.m4a",)


def test_keeps_backup_urls(monkeypatch):
    _client_returning(
        monkeypatch,
        {
            "playurl": {
                "code": 0,
                "data": {
                    "dash": {
                        "audio": [
                            {
                                "id": 30280,
                                "bandwidth": 1,
                                "baseUrl": "https://cdn/a.m4a",
                                "backupUrl": ["https://cdn/b.m4a", "https://cdn/c.m4a"],
                            }
                        ]
                    }
                },
            }
        },
    )

    stream = bili_playurl.get_audio_stream("BV1xx", cid=123)
    assert stream.backup_urls == ("https://cdn/b.m4a", "https://cdn/c.m4a")


def test_raises_when_no_audio_track(monkeypatch):
    """有些视频只有 durl（没有 DASH），此时应抛异常让调用方回退 yt-dlp。"""
    _client_returning(monkeypatch, {"playurl": {"code": 0, "data": {"dash": {"audio": []}}}})

    with pytest.raises(ValueError, match="未返回 DASH 音频轨"):
        bili_playurl.get_audio_stream("BV1xx", cid=123)


def test_raises_on_bilibili_error_code(monkeypatch):
    _client_returning(monkeypatch, {"playurl": {"code": -404, "message": "啥都木有"}})

    with pytest.raises(ValueError, match="code=-404"):
        bili_playurl.get_audio_stream("BV1xx", cid=123)


def test_raises_when_base_url_missing(monkeypatch):
    _client_returning(
        monkeypatch,
        {"playurl": {"code": 0, "data": {"dash": {"audio": [{"id": 30280, "bandwidth": 1}]}}}},
    )

    with pytest.raises(ValueError, match="缺少 baseUrl"):
        bili_playurl.get_audio_stream("BV1xx", cid=123)


def test_resolve_cid(monkeypatch):
    _client_returning(
        monkeypatch, {"view": {"code": 0, "data": {"aid": 1, "cid": 999, "title": "t"}}}
    )

    assert bili_playurl.resolve_cid("BV1xx") == 999


def test_cdn_headers_include_ua_referer_and_cookie(monkeypatch):
    """CDN 实测：缺 UA/Referer/Cookie 任意一个都是 403。"""
    headers = bili_playurl._headers()

    assert headers["User-Agent"]
    assert headers["Referer"].startswith("https://www.bilibili.com")
    assert headers["Cookie"] == "buvid3=TEST"


# ---- audio.download_audio 走 API 优先 ----


class _FakeMediaResponse:
    def __init__(self, chunks, headers=None):
        self._chunks = chunks
        self.headers = headers or {"content-type": "video/mp4"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self, chunk_size):
        yield from self._chunks


def _stub_stream(monkeypatch, response, captured: dict | None = None):
    def _stream(method, url, **kwargs):
        if captured is not None:
            captured.update({"url": url, **kwargs})
        return response

    monkeypatch.setattr(audio_module.httpx, "stream", _stream)


def _stub_playurl(monkeypatch, stream: bili_playurl.AudioStream):
    monkeypatch.setattr(bili_playurl, "get_audio_stream", lambda *a, **kw: stream)


def test_download_audio_prefers_playurl_api(monkeypatch):
    """API 路径命中时不该再去碰 yt-dlp（那会抓网页，机房 IP 必 412）。"""
    _stub_playurl(
        monkeypatch,
        bili_playurl.AudioStream(
            url="https://cdn/audio.m4a", backup_urls=(), mime_type="audio/mp4", bandwidth=1
        ),
    )
    _stub_stream(monkeypatch, _FakeMediaResponse([b"abc", b"def"]))

    def _no_ytdlp(*args, **kwargs):
        raise AssertionError("playurl 已命中，不该回退 yt-dlp")

    monkeypatch.setattr(audio_module.yt_dlp, "YoutubeDL", _no_ytdlp)

    buffer = audio_module.download_audio("https://www.bilibili.com/video/BV1GJ411x7h7")
    assert buffer.read() == b"abcdef"


def test_playurl_request_carries_cdn_headers(monkeypatch):
    """CDN 需要 UA + Referer + Cookie，少一个就 403。"""
    _stub_playurl(
        monkeypatch,
        bili_playurl.AudioStream(
            url="https://cdn/audio.m4a", backup_urls=(), mime_type="", bandwidth=1
        ),
    )
    captured: dict = {}
    _stub_stream(monkeypatch, _FakeMediaResponse([b"x"]), captured)

    audio_module.download_audio("https://www.bilibili.com/video/BV1GJ411x7h7")

    headers = captured["headers"]
    assert headers["Cookie"] == "buvid3=TEST"
    assert headers["Referer"].startswith("https://www.bilibili.com")
    assert headers["User-Agent"]


def test_falls_back_to_backup_urls(monkeypatch):
    _stub_playurl(
        monkeypatch,
        bili_playurl.AudioStream(
            url="https://cdn/broken.m4a",
            backup_urls=("https://cdn/ok.m4a",),
            mime_type="",
            bandwidth=1,
        ),
    )

    calls: list[str] = []

    def _stream(method, url, **kwargs):
        calls.append(url)
        if "broken" in url:
            raise httpx.ConnectError("boom")
        return _FakeMediaResponse([b"backup-data"])

    monkeypatch.setattr(audio_module.httpx, "stream", _stream)

    buffer = audio_module.download_audio("https://www.bilibili.com/video/BV1GJ411x7h7")
    assert buffer.read() == b"backup-data"
    assert calls == ["https://cdn/broken.m4a", "https://cdn/ok.m4a"]


def test_rejects_html_error_page_instead_of_using_it_as_audio(monkeypatch):
    """CDN 缺请求头会返回 403 + text/html；绝不能当音频喂给 ASR。"""
    from app.errors import ParseFailedError

    _stub_playurl(
        monkeypatch,
        bili_playurl.AudioStream(
            url="https://cdn/audio.m4a", backup_urls=(), mime_type="", bandwidth=1
        ),
    )
    _stub_stream(
        monkeypatch,
        _FakeMediaResponse([b"<html>403</html>"], {"content-type": "text/html"}),
    )

    # 所有地址都是 HTML → ValueError → 回退 yt-dlp；让 yt-dlp 也失败，
    # 从而确认「HTML 错误页没有被当成功」
    monkeypatch.setattr(
        audio_module.yt_dlp,
        "YoutubeDL",
        lambda *a, **kw: (_ for _ in ()).throw(
            audio_module.yt_dlp.utils.DownloadError("ERROR: no video")
        ),
    )

    with pytest.raises(ParseFailedError) as excinfo:
        audio_module.download_audio("https://www.bilibili.com/video/BV1GJ411x7h7")

    # 走的是 yt-dlp 的失败路径，而不是把 "<html>403</html>" 当音频返回
    assert "视频" in str(excinfo.value) or "音频" in str(excinfo.value)


def test_accepts_octet_stream_as_media(monkeypatch):
    """回归：云端 CDN 把 mp4 报成 application/octet-stream（本地是 video/mp4）。

    早期实现只认 audio/* 与 video/*，在云端把合法音频误判成「错误页」，
    于是白白回退到 yt-dlp —— 而 yt-dlp 因网页 412 必然失败，最终用户看到
    的是误导性的 412。这个差异只在云端出现，本地复现不了。
    """
    _stub_playurl(
        monkeypatch,
        bili_playurl.AudioStream(
            url="https://cdn/audio.m4a", backup_urls=(), mime_type="", bandwidth=1
        ),
    )
    _stub_stream(
        monkeypatch,
        _FakeMediaResponse(
            [b"\x00\x00\x00\x18ftypmp42"],
            {"content-type": "application/octet-stream", "content-length": "12"},
        ),
    )

    def _no_ytdlp(*args, **kwargs):
        raise AssertionError("octet-stream 是合法媒体类型，不该回退 yt-dlp")

    monkeypatch.setattr(audio_module.yt_dlp, "YoutubeDL", _no_ytdlp)

    buffer = audio_module.download_audio("https://www.bilibili.com/video/BV1GJ411x7h7")
    assert buffer.read().startswith(b"\x00\x00\x00\x18ftyp")


def test_accepts_empty_content_type(monkeypatch):
    """有些 CDN 节点干脆不给 content-type，同样不能判成错误页。"""
    _stub_playurl(
        monkeypatch,
        bili_playurl.AudioStream(
            url="https://cdn/audio.m4a", backup_urls=(), mime_type="", bandwidth=1
        ),
    )
    _stub_stream(monkeypatch, _FakeMediaResponse([b"audio-bytes"], {}))

    def _no_ytdlp(*args, **kwargs):
        raise AssertionError("缺 content-type 不该判成错误页")

    monkeypatch.setattr(audio_module.yt_dlp, "YoutubeDL", _no_ytdlp)

    assert (
        audio_module.download_audio("https://www.bilibili.com/video/BV1GJ411x7h7").read()
        == b"audio-bytes"
    )


def test_skips_api_for_non_bilibili_url(monkeypatch):
    """非 B站链接不该走 playurl（detect_platform 直接判定不支持）。"""
    from app.services import audio as mod

    monkeypatch.setattr(bili_playurl, "get_audio_stream", lambda *a, **kw: pytest.fail("不该调用"))

    with pytest.raises(Exception):
        mod._download_via_playurl("https://example.com/video/1", 1024 * 1024)


def test_skips_api_when_bvid_missing(monkeypatch):
    """B站但不是 /video/BVxxx 形式（如番剧 ep），跳过 API 交给 yt-dlp。"""
    monkeypatch.setattr(bili_playurl, "get_audio_stream", lambda *a, **kw: pytest.fail("不该调用"))

    with pytest.raises(ValueError, match="BV"):
        audio_module._download_via_playurl(
            "https://www.bilibili.com/bangumi/play/ep123456", 1024 * 1024
        )


def test_oversize_via_api_raises_without_fallback(monkeypatch):
    """超限是明确错误，不该悄悄回退（否则白下载一次大文件）。"""
    from app.errors import AudioTooLargeError

    _stub_playurl(
        monkeypatch,
        bili_playurl.AudioStream(
            url="https://cdn/audio.m4a", backup_urls=(), mime_type="", bandwidth=1
        ),
    )
    _stub_stream(
        monkeypatch,
        _FakeMediaResponse([b"x" * 100], {"content-type": "audio/mp4", "content-length": "999999999"}),
    )

    with pytest.raises(AudioTooLargeError):
        audio_module.download_audio("https://www.bilibili.com/video/BV1GJ411x7h7")
