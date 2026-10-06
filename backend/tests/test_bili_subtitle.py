"""B站字幕快路径（弹幕元数据接口 / 分层降级）测试。

- 无标记：全 mock，不访问外网（dm/view 响应结构、降级链、来源标记、白名单）
- @pytest.mark.network：真实请求 B站接口验证未登录 AI 字幕全链路（CI 跳过）
"""
import json
from pathlib import Path

import httpx
import pytest

import app.services.bili_player_api as bili
import app.services.subtitles as subtitles
from app.services.bili_player_api import BiliSubtitleTrack, get_subtitle_tracks, get_video_info
from app.services.subtitles import is_allowed_subtitle_url, try_extract_subtitle_transcript

# ---- 测试数据（与真实响应结构一致，见侦察报告）----

VIEW_PAYLOAD = {
    "code": 0,
    "data": {"aid": 117364930643634, "cid": 42365094446, "title": "【短的发布会】iQOO 电竞宇宙来了"},
}

DM_VIEW_PAYLOAD = {
    "code": 0,
    "data": {
        "subtitle": {
            "subtitles": [
                {"lan": "ai-en", "lan_doc": "英语（自动翻译）", "ai_status": 2, "subtitle_url": "http://aisubtitle.hdslb.com/en.json?auth_key=x"},
                {"lan": "ai-zh", "lan_doc": "中文（自动生成）", "ai_status": 2, "subtitle_url": "http://aisubtitle.hdslb.com/zh.json?auth_key=x"},
                {"lan": "ai-ja", "lan_doc": "日文（生成中）", "ai_status": 1, "subtitle_url": "http://aisubtitle.hdslb.com/ja.json?auth_key=x"},
                {"lan": "zh-CN", "lan_doc": "中文（UP主手传）", "ai_status": 0, "subtitle_url": "http://aisubtitle.hdslb.com/cc.json?auth_key=x"},
            ]
        }
    },
}

SUBTITLE_BODY = {"body": [{"from": 0.08, "to": 1.2, "content": "短的发布会"}, {"from": 1.44, "to": 3.0, "content": "会绕这一场"}]}


def _patch_responses(monkeypatch, mapping: dict[str, dict]):
    """按 URL 前缀路由假响应，替换 bili_player_api._get_json（返回 payload dict）。"""

    def fake_get_json(client, url):
        for prefix, payload in mapping.items():
            if prefix in url:
                return payload
        return None

    monkeypatch.setattr(bili, "_get_json", fake_get_json)


# ---- bili_player_api ----

def test_get_video_info_parses_aid_cid(monkeypatch):
    _patch_responses(monkeypatch, {"web-interface/view": VIEW_PAYLOAD})
    info = get_video_info("BV1GJ411x7h7")
    assert info == {"aid": 117364930643634, "cid": 42365094446, "title": "【短的发布会】iQOO 电竞宇宙来了"}


def test_get_video_info_failure_returns_none(monkeypatch):
    _patch_responses(monkeypatch, {"web-interface/view": {"code": -404, "message": "啥都木有"}})
    assert get_video_info("BV1nonexist") is None


def test_get_subtitle_tracks_filters_ai_status_and_sorts_zh_first(monkeypatch):
    """ai_status=2 的 AI 轨道才收录（0/1 跳过）；CC 优先于 AI、中文优先。"""
    _patch_responses(monkeypatch, {"web-interface/view": VIEW_PAYLOAD, "v2/dm/view": DM_VIEW_PAYLOAD})
    tracks = get_subtitle_tracks("BV1GJ411x7h7")
    # ai-ja（status=1，未生成）被过滤；剩余 CC(zh-CN) > AI(ai-zh) > AI(ai-en)
    assert [t.lan for t in tracks] == ["zh-CN", "ai-zh", "ai-en"]
    assert all(t.subtitle_url.startswith("https://") for t in tracks)  # http → https 升级
    assert [t.is_ai for t in tracks] == [False, True, True]


def test_get_subtitle_tracks_empty_when_no_subtitles(monkeypatch):
    empty = {"code": 0, "data": {"subtitle": {"subtitles": []}}}
    _patch_responses(monkeypatch, {"web-interface/view": VIEW_PAYLOAD, "v2/dm/view": empty})
    assert get_subtitle_tracks("BV1GJ411x7h7") == []


def test_get_subtitle_tracks_swallows_request_failure(monkeypatch):
    def failing_get_json(client, url):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(bili, "_get_json", failing_get_json)
    assert get_subtitle_tracks("BV1GJ411x7h7") == []


# ---- 字幕 URL 白名单 ----

def test_subtitle_url_whitelist():
    assert is_allowed_subtitle_url("https://aisubtitle.hdslb.com/bfs/ai_subtitle/prod/x?auth_key=y")
    assert is_allowed_subtitle_url("https://api.bilibili.com/x/report")
    assert not is_allowed_subtitle_url("https://evil.example.com/subtitle.json")
    assert not is_allowed_subtitle_url("http://127.0.0.1/subtitle.json")


def test_download_track_items_rejects_non_whitelisted(monkeypatch):
    """白名单外 URL 直接拒绝，不发起任何请求。"""
    requested = []
    monkeypatch.setattr(subtitles.httpx, "Client", lambda **kwargs: requested.append(kwargs) or (_ for _ in ()).throw(AssertionError("不应发起请求")))
    assert subtitles._download_track_items("https://evil.example.com/sub.json", "json", 5) is None
    assert not requested


# ---- 分层降级 ----

def _fake_ydl(monkeypatch, info_or_exc):
    """替换 subtitles 模块的 yt_dlp.YoutubeDL：返回 info 或抛异常。"""
    class FakeYDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            if isinstance(info_or_exc, Exception):
                raise info_or_exc
            return info_or_exc

    monkeypatch.setattr(subtitles.yt_dlp, "YoutubeDL", FakeYDL)


CC_INFO = {"subtitles": {"zh-CN": [{"url": "https://aisubtitle.hdslb.com/cc.json", "ext": "json"}]}, "automatic_captions": {}}


def test_layered_extract_hits_cc_first(monkeypatch):
    """层 1 yt-dlp CC 命中 → source=subtitle_cc，不再走 dm/view。"""
    _fake_ydl(monkeypatch, CC_INFO)
    calls = []
    monkeypatch.setattr(bili, "get_subtitle_tracks", lambda bvid: calls.append(bvid) or [])
    monkeypatch.setattr(subtitles, "_download_track_items", lambda url, ext, timeout: [{"time": 1.0, "text": "CC"}])
    result = try_extract_subtitle_transcript("https://www.bilibili.com/video/BV1GJ411x7h7")
    assert result == ([{"time": 1.0, "text": "CC"}], "subtitle_cc")
    assert not calls  # 层 1 命中，层 2 未被调用


def test_layered_extract_falls_back_to_dm_view_ai(monkeypatch):
    """层 1 无 CC → 降级层 2 dm/view AI 字幕 → source=subtitle_ai。"""
    _fake_ydl(monkeypatch, {"subtitles": {}, "automatic_captions": {}})
    monkeypatch.setattr(bili, "get_subtitle_tracks", lambda bvid: [BiliSubtitleTrack("ai-zh", "https://aisubtitle.hdslb.com/zh.json", True)])
    monkeypatch.setattr(subtitles, "_download_track_items", lambda url, ext, timeout: [{"time": 0.08, "text": "AI字幕"}])
    result = try_extract_subtitle_transcript("https://www.bilibili.com/video/BV1GJ411x7h7")
    assert result == ([{"time": 0.08, "text": "AI字幕"}], "subtitle_ai")


def test_layered_extract_returns_none_when_all_layers_fail(monkeypatch):
    """两层全部失败（含异常）→ None，上层走 ASR，不抛异常。"""
    _fake_ydl(monkeypatch, RuntimeError("yt-dlp boom"))
    monkeypatch.setattr(bili, "get_subtitle_tracks", lambda bvid: (_ for _ in ()).throw(RuntimeError("dm boom")))
    result = try_extract_subtitle_transcript("https://www.bilibili.com/video/BV1GJ411x7h7")
    assert result is None


def test_layered_extract_skips_unparseable_tracks(monkeypatch):
    """轨道下载后解析失败（None）→ 继续尝试后续轨道/层，不误报命中。"""
    _fake_ydl(monkeypatch, {"subtitles": {}, "automatic_captions": {}})
    monkeypatch.setattr(bili, "get_subtitle_tracks", lambda bvid: [BiliSubtitleTrack("ai-zh", "https://aisubtitle.hdslb.com/bad.json", True)])
    monkeypatch.setattr(subtitles, "_download_track_items", lambda url, ext, timeout: None)
    assert try_extract_subtitle_transcript("https://www.bilibili.com/video/BV1GJ411x7h7") is None


def test_extract_bvid_parsing():
    assert subtitles._extract_bvid("https://www.bilibili.com/video/BV1GJ411x7h7/") == "BV1GJ411x7h7"
    assert subtitles._extract_bvid("https://www.bilibili.com/video/av116187438517161") is None  # 仅 BV 号（av 号走 bvid 参数无效，侦察实证）
    assert subtitles._extract_bvid("https://www.youtube.com/watch?v=x") is None


def test_download_track_items_upgrades_to_https(monkeypatch):
    """http URL 下载时升级 https；响应体经解析器转 items。"""
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        @property
        def text(self):
            return json.dumps(SUBTITLE_BODY, ensure_ascii=False)

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            captured["url"] = url
            return FakeResponse()

    monkeypatch.setattr(subtitles.httpx, "Client", FakeClient)
    items = subtitles._download_track_items("http://aisubtitle.hdslb.com/zh.json?auth_key=x", "json", 5)
    assert items == [{"time": 0.08, "text": "短的发布会"}, {"time": 1.44, "text": "会绕这一场"}]
    assert captured["url"].startswith("https://aisubtitle.hdslb.com/")


# ---- 真实集成（CI 跳过）：未登录 AI 字幕全链路 ----

@pytest.mark.network
def test_dm_view_real_ai_subtitle_end_to_end():
    """真实请求：未登录（零 Cookie）→ view → dm/view → 下载 → 解析。

    请求数 3（view/dm-view/字幕下载），含 1.5s 间隔约 8-10s。
    运行：cd backend && .venv\\Scripts\\activate && pytest -m network tests/test_bili_subtitle.py -v
    """
    result = try_extract_subtitle_transcript("https://www.bilibili.com/video/BV1S6aB63Er7")
    assert result is not None, "dm/view 未登录字幕链路失败（接口可能改版，见 docs/bili-subtitle-recon.md）"
    items, source = result
    assert source in ("subtitle_ai", "subtitle_cc")
    assert len(items) > 50  # 150 秒发布会实测 171 条
    assert all("time" in item and "text" in item for item in items[:10])
