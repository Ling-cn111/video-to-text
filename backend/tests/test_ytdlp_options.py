"""`app/services/ytdlp_options.py`：公共 yt-dlp 选项的契约。

这个模块是「四个调用点必须一致」的那部分选项的单一来源。它承担两件事：

1. **永远关颜色**（`no_color`）——报错会进 `error.message`，见
   `test_error_message_hygiene.py`。
2. **机房 IP 的风控缓解**——带上与真实浏览器一致的 Referer /
   Accept-Language，必要时注入 BILI_COOKIE。云端实测：阿里云出口访问 B站
   会报 HTTP 412 Precondition Failed。
"""
import pytest

from app import config
from app.services import ytdlp_options


def test_base_options_always_disable_color():
    assert ytdlp_options.base_options()["no_color"] is True


def test_base_options_are_quiet_by_default():
    """库用法不该往 stdout 打进度（会污染 worker 日志）。"""
    opts = ytdlp_options.base_options()
    assert opts["quiet"] is True
    assert opts["no_warnings"] is True


def test_base_options_send_browser_like_headers():
    """Referer 是 B站 CDN 的硬性要求；缺了容易被当成爬虫。"""
    headers = ytdlp_options.base_options()["http_headers"]
    assert headers["Referer"].startswith("https://www.bilibili.com")
    assert "zh-CN" in headers["Accept-Language"]


def test_base_options_have_no_cookie_by_default(monkeypatch):
    """默认绝不能带 Cookie：它是账号凭证，只能显式配置。"""
    monkeypatch.setattr(config, "BILI_COOKIE", "", raising=False)
    headers = ytdlp_options.base_options()["http_headers"]
    assert "Cookie" not in headers


def test_cookie_injected_when_configured(monkeypatch):
    monkeypatch.setattr(config, "BILI_COOKIE", "SESSDATA=abc; bili_jct=def", raising=False)
    headers = ytdlp_options.base_options()["http_headers"]
    assert headers["Cookie"] == "SESSDATA=abc; bili_jct=def"


def test_user_agent_overridden_when_configured(monkeypatch):
    """配了 BILI_USER_AGENT 就用它，否则沿用 yt-dlp 默认 UA。"""
    monkeypatch.setattr(config, "BILI_USER_AGENT", "CustomUA/1.0", raising=False)
    headers = ytdlp_options.base_options()["http_headers"]
    assert headers["User-Agent"] == "CustomUA/1.0"


def test_user_agent_absent_by_default(monkeypatch):
    monkeypatch.setattr(config, "BILI_USER_AGENT", "", raising=False)
    headers = ytdlp_options.base_options()["http_headers"]
    assert "User-Agent" not in headers, (
        "默认不要显式设置 UA，让 yt-dlp 用它自带的（它会随版本更新）"
    )


def test_returns_a_fresh_dict_each_call():
    """调用方会用 `{**base_options(), ...}` 覆盖；共享同一个 dict 会互相污染。"""
    first = ytdlp_options.base_options()
    first["http_headers"]["Referer"] = "mutated"
    first["custom_key"] = 1

    second = ytdlp_options.base_options()
    assert second["http_headers"]["Referer"] != "mutated"
    assert "custom_key" not in second


def test_socket_timeout_follows_config(monkeypatch):
    monkeypatch.setattr(config, "EXTRACT_TIMEOUT_SECONDS", 17, raising=False)
    assert ytdlp_options.base_options()["socket_timeout"] == 17


@pytest.mark.parametrize(
    "module_name",
    ["app.services.parser", "app.services.audio", "app.services.subtitles"],
)
def test_call_sites_inherit_base_options(module_name):
    """各调用点必须基于公共选项构建，不能自己另起一套。"""
    import importlib

    module = importlib.import_module(module_name)
    source = module.__file__
    assert source is not None

    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "_base_options()" in text, (
        f"{module_name} 没有使用公共 yt-dlp 选项（no_color / 风控头会漏掉）"
    )
