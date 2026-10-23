"""`app/services/bili_fingerprint.py`：B站设备指纹的获取、缓存与降级。

背景（云端实测，2026-10-23）：

    POST /internal/worker/diagnose
    {"view_api":{"status":412},"video_page":{"status":412},"finger_spi":{"status":200}}

B站并没有封掉整台机器——设备指纹接口是通的，封的是「没带 buvid 的请求」。
因此这里的关键契约有三条：

1. 能取到指纹时，拼成 `buvid3=...; buvid4=...` 的 Cookie 片段；
2. **取失败必须降级为空字符串**，绝不能让指纹问题反过来把转写打挂；
3. 取失败要进冷却期，避免被风控时每来一个任务就打一次指纹接口
   （那本身是可疑行为，会让风控更严）。

本文件不联网：httpx.get 全部被替换。
"""
import httpx
import pytest

from app.services import bili_fingerprint


@pytest.fixture(autouse=True)
def _reset_cache():
    """每个用例都从冷缓存开始，避免相互污染。"""
    bili_fingerprint.reset_cache()
    yield
    bili_fingerprint.reset_cache()


def _fake_response(payload, status_code=200):
    def _json():
        return payload

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            if status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"status {status_code}", request=None, response=None
                )

        def json(self):
            return _json()

    return _Resp()


def _patch_get(monkeypatch, payload=None, *, exc=None, status_code=200):
    calls: list[str] = []

    def _get(url, **kwargs):
        calls.append(url)
        if exc is not None:
            raise exc
        return _fake_response(payload, status_code)

    monkeypatch.setattr(bili_fingerprint.httpx, "get", _get)
    return calls


def test_returns_cookie_fragment_from_b_3_and_b_4(monkeypatch):
    _patch_get(monkeypatch, {"code": 0, "data": {"b_3": "ABC", "b_4": "DEF"}})

    assert bili_fingerprint.device_cookie() == "buvid3=ABC; buvid4=DEF"


def test_works_with_only_b_3(monkeypatch):
    """b_4 缺失时也应有可用指纹（buvid3 才是必需的）。"""
    _patch_get(monkeypatch, {"code": 0, "data": {"b_3": "ABC"}})

    assert bili_fingerprint.device_cookie() == "buvid3=ABC"


def test_returns_empty_string_when_network_fails(monkeypatch):
    """核心契约：网络失败不能抛异常，否则整个转写会因「缓解手段」而失败。"""
    _patch_get(monkeypatch, exc=httpx.ConnectTimeout("timed out"))

    assert bili_fingerprint.device_cookie() == ""


def test_returns_empty_string_when_bilibili_returns_error_code(monkeypatch):
    """code != 0 说明请求被风控拒绝，此时没有可用指纹。"""
    _patch_get(monkeypatch, {"code": -352, "message": "risk control"})

    assert bili_fingerprint.device_cookie() == ""


def test_returns_empty_string_when_b_3_missing(monkeypatch):
    _patch_get(monkeypatch, {"code": 0, "data": {"b_4": "DEF"}})

    assert bili_fingerprint.device_cookie() == ""


def test_caches_successful_result(monkeypatch):
    """指纹有效期很长，不该每个任务都去请求一次（频繁请求本身像爬虫）。"""
    calls = _patch_get(monkeypatch, {"code": 0, "data": {"b_3": "ABC", "b_4": "DEF"}})

    bili_fingerprint.device_cookie()
    bili_fingerprint.device_cookie()
    bili_fingerprint.device_cookie()

    assert len(calls) == 1


def test_failure_is_cached_for_cooldown(monkeypatch):
    """失败也要缓存一小段：被风控时不能每个任务都去打一次指纹接口。"""
    calls = _patch_get(monkeypatch, exc=httpx.ConnectError("boom"))

    assert bili_fingerprint.device_cookie() == ""
    assert bili_fingerprint.device_cookie() == ""

    assert len(calls) == 1, "失败后应进冷却期，不该每次调用都重试"


def test_reset_cache_forces_refetch(monkeypatch):
    calls = _patch_get(monkeypatch, {"code": 0, "data": {"b_3": "ABC"}})

    bili_fingerprint.device_cookie()
    bili_fingerprint.reset_cache()
    bili_fingerprint.device_cookie()

    assert len(calls) == 2


def test_recovers_after_cooldown(monkeypatch):
    """冷却期过后（或 reset 后）应能重新取到指纹，不能永久卡在失败状态。"""
    _patch_get(monkeypatch, exc=httpx.ConnectError("boom"))
    assert bili_fingerprint.device_cookie() == ""

    bili_fingerprint.reset_cache()
    _patch_get(monkeypatch, {"code": 0, "data": {"b_3": "RECOVERED"}})
    assert bili_fingerprint.device_cookie() == "buvid3=RECOVERED"


def test_sends_browser_like_headers(monkeypatch):
    """取指纹本身也要带 Referer：裸请求同样可能被 412。"""
    captured: dict = {}

    def _get(url, **kwargs):
        captured.update(kwargs.get("headers") or {})
        return _fake_response({"code": 0, "data": {"b_3": "ABC"}})

    monkeypatch.setattr(bili_fingerprint.httpx, "get", _get)
    bili_fingerprint.device_cookie()

    assert captured["Referer"].startswith("https://www.bilibili.com")
    assert "User-Agent" in captured
