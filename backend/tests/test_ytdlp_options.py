"""`app/services/ytdlp_options.py`：公共 yt-dlp 选项的契约。

这个模块是「四个调用点必须一致」的那部分选项的单一来源。它承担两件事：

1. **永远关颜色**（`no_color`）——报错会进 `error.message`，见
   `test_error_message_hygiene.py`。
2. **机房 IP 的风控缓解**——带上 Referer / Accept-Language / 设备指纹 Cookie，
   必要时再用账号 Cookie 覆盖。云端实测：阿里云出口裸请求访问 B站
   view API 与视频页都是 HTTP 412 Precondition Failed，而设备指纹接口 200。

本文件里所有用例都不联网：`_no_network` fixture 默认把指纹取成空字符串，
需要指纹的用例再显式给值。指纹获取本身的测试在
`test_bili_fingerprint.py`。
"""
import pytest

from app import config
from app.services import bili_fingerprint, ytdlp_options


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """默认不联网：指纹取不到（等价于「本地开发/指纹接口挂了」）。"""
    monkeypatch.setattr(bili_fingerprint, "device_cookie", lambda: "")
    monkeypatch.setattr(config, "BILI_COOKIE", "", raising=False)
    monkeypatch.setattr(config, "BILI_USER_AGENT", "", raising=False)


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


def test_no_cookie_when_nothing_available(monkeypatch):
    """既没有账号 Cookie 又取不到指纹时，不要发空的 Cookie 头。"""
    monkeypatch.setattr(bili_fingerprint, "device_cookie", lambda: "")
    headers = ytdlp_options.base_options()["http_headers"]
    assert "Cookie" not in headers


def test_device_fingerprint_injected_when_available(monkeypatch):
    """云端修复的核心：没有账号 Cookie 时，用设备指纹顶上。

    云端实测（阿里云 cn-hangzhou）：不带 buvid 的请求 view API 与视频页都是
    412；带上 buvid3/buvid4 后转写链路恢复。它不含账号凭证，可自动获取。
    """
    monkeypatch.setattr(
        bili_fingerprint, "device_cookie", lambda: "buvid3=ABC123; buvid4=DEF456"
    )
    headers = ytdlp_options.base_options()["http_headers"]
    assert headers["Cookie"] == "buvid3=ABC123; buvid4=DEF456"


def test_account_cookie_takes_precedence_over_fingerprint(monkeypatch):
    """配了账号 Cookie 就不混入指纹：已登录 Cookie 自带 buvid3，
    混入另一设备的指纹反而可能被判「Cookie 与设备不一致」。
    """
    calls: list[int] = []

    def _spy():
        calls.append(1)
        return "buvid3=SHOULD_NOT_APPEAR"

    monkeypatch.setattr(bili_fingerprint, "device_cookie", _spy)
    monkeypatch.setattr(config, "BILI_COOKIE", "SESSDATA=abc; bili_jct=def", raising=False)

    headers = ytdlp_options.base_options()["http_headers"]
    assert headers["Cookie"] == "SESSDATA=abc; bili_jct=def"
    assert calls == [], "配了账号 Cookie 就不该再去取指纹（多一次无谓的网络请求）"


def test_user_agent_overridden_when_configured(monkeypatch):
    """配了 BILI_USER_AGENT 就用它，否则沿用 yt-dlp 默认 UA。"""
    monkeypatch.setattr(config, "BILI_USER_AGENT", "CustomUA/1.0", raising=False)
    headers = ytdlp_options.base_options()["http_headers"]
    assert headers["User-Agent"] == "CustomUA/1.0"


def test_user_agent_absent_by_default():
    headers = ytdlp_options.base_options()["http_headers"]
    assert "User-Agent" not in headers, (
        "默认不要显式设置 UA，让 yt-dlp 用它自带的（它会随版本更新）"
    )


def test_returns_a_fresh_dict_each_call(monkeypatch):
    """调用方会用 `{**base_options(), ...}` 覆盖；共享同一个 dict 会互相污染。"""
    monkeypatch.setattr(bili_fingerprint, "device_cookie", lambda: "buvid3=ABC")

    first = ytdlp_options.base_options()
    first["http_headers"]["Referer"] = "mutated"
    first["http_headers"]["Cookie"] = "mutated"
    first["custom_key"] = 1

    second = ytdlp_options.base_options()
    assert second["http_headers"]["Referer"] != "mutated"
    assert second["http_headers"]["Cookie"] != "mutated"
    assert "custom_key" not in second


def test_socket_timeout_follows_config(monkeypatch):
    monkeypatch.setattr(config, "EXTRACT_TIMEOUT_SECONDS", 17, raising=False)
    assert ytdlp_options.base_options()["socket_timeout"] == 17


def test_options_are_built_at_call_time_not_import_time(monkeypatch):
    """指纹是动态的，选项必须在**调用时**重新构建。

    回归：以前 `parser` 用模块级 `_YDL_OPTIONS = {**_base_options(), ...}`，
    导入时算一次就固定下来——冷启动时取到的指纹会一直用到进程结束，
    指纹失效后无法自愈。
    """
    monkeypatch.setattr(bili_fingerprint, "device_cookie", lambda: "buvid3=FIRST")
    assert ytdlp_options.base_options()["http_headers"]["Cookie"] == "buvid3=FIRST"

    monkeypatch.setattr(bili_fingerprint, "device_cookie", lambda: "buvid3=SECOND")
    assert ytdlp_options.base_options()["http_headers"]["Cookie"] == "buvid3=SECOND"


def test_parser_builds_options_per_call(monkeypatch):
    """parser 尤其不能缓存：它是任务的第一步，指纹变了必须立刻生效。"""
    from app.services import parser

    assert not hasattr(parser, "_YDL_OPTIONS"), (
        "parser 又在模块级缓存了 yt-dlp 选项；指纹无法刷新"
    )

    monkeypatch.setattr(bili_fingerprint, "device_cookie", lambda: "buvid3=PARSER")
    assert parser._ydl_options()["http_headers"]["Cookie"] == "buvid3=PARSER"


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
