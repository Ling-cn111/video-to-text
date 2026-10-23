"""错误消息卫生：不能把终端 ANSI 颜色码透给前端。

为什么单独立一条契约：yt-dlp / ffmpeg 的报错会往 stderr 写带颜色的日志，
这些文本被拼进 `error.message` 后原样发给前端，界面上就会出现

    "<ESC>[0;31mERROR:<ESC>[0m [BiliBili] Unable to download webpage ..."

这类乱码。云端实测确认过：B 站 412 的失败信息里确实混进了 SGR 颜色码。
两道防线：

1. 源头：所有 `yt_dlp.YoutubeDL` 调用都带 `no_color: True`；
2. 出口：`AppException` 构造时统一 `strip_ansi`（覆盖任何来源，含 ffmpeg）。
"""
import re
from pathlib import Path

import pytest

from app.errors import AppException, ParseFailedError, strip_ansi

APP_DIR = Path(__file__).resolve().parents[1] / "app"
# 真实报错样本：云端 transcribe 失败时 error.message 的原文片段
COLORED = (
    "\x1b[0;31mERROR:\x1b[0m [BiliBili] 1GJ411x7h7: Unable to download webpage: "
    "HTTP Error 412: Precondition Failed"
)


def test_strip_ansi_removes_sgr_sequences():
    cleaned = strip_ansi(COLORED)
    assert "\x1b" not in cleaned
    assert "ERROR:" in cleaned
    assert "HTTP Error 412" in cleaned


def test_strip_ansi_leaves_plain_text_untouched():
    text = "音频下载失败：视频不存在"
    assert strip_ansi(text) == text


def test_strip_ansi_tolerates_non_string():
    assert strip_ansi(None) is None


def test_app_exception_cleans_message_on_construction():
    """出口兜底：即使调用方忘了清洗，也不该把颜色码带出去。"""
    exc = ParseFailedError(COLORED)
    assert "\x1b" not in exc.message
    assert "\x1b" not in str(exc)
    assert "HTTP Error 412" in exc.message


def test_app_exception_keeps_code_and_status():
    """清洗不能顺手改掉错误码/状态码（回归保护）。"""
    exc = AppException(code="SOME_CODE", message=COLORED, status_code=418)
    assert exc.code == "SOME_CODE"
    assert exc.status_code == 418


def _record_youtubedl_params(monkeypatch):
    """拦截 `yt_dlp.YoutubeDL` 实际收到的参数，返回用于事后断言的记录列表。

    静态扫源码很脆弱（选项字典可能是内联、局部变量或模块常量）。这里直接替换
    `YoutubeDL`，让真实调用路径把 params 交出来——新增调用点漏设 no_color 时，
    只要它真的被走到就会暴露。

    同时把 playurl API 路径打桩成失败：它是 B站专用的前置路径（纯 JSON，
    不经过 yt-dlp），不打桩就会真的发网络请求，既慢又不稳定。
    """
    import yt_dlp

    from app.services import bili_playurl

    recorded: list[dict] = []

    monkeypatch.setattr(
        bili_playurl,
        "get_audio_stream",
        lambda *a, **kw: (_ for _ in ()).throw(ValueError("stubbed out")),
    )

    class _Spy:
        def __init__(self, params=None, *args, **kwargs):
            recorded.append(dict(params or {}))

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def extract_info(self, *args, **kwargs):
            # 让调用方走进 except DownloadError 分支，从而复现「把 yt-dlp 报错
            # 拼进 message」的真实路径
            raise yt_dlp.utils.DownloadError(f"{COLORED}")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _Spy)
    return recorded


def test_parse_video_url_disables_color(monkeypatch):
    from app.services import parser

    recorded = _record_youtubedl_params(monkeypatch)
    with pytest.raises(AppException):
        parser.parse_video_url("https://www.bilibili.com/video/BV1GJ411x7h7")

    assert recorded, "parse_video_url 没有调用 YoutubeDL，测试失去意义"
    for params in recorded:
        assert params.get("no_color") is True, f"解析路径未关颜色：{params}"


def test_download_audio_disables_color(monkeypatch):
    from app.services import audio

    recorded = _record_youtubedl_params(monkeypatch)
    with pytest.raises(AppException):
        audio.download_audio("https://www.bilibili.com/video/BV1GJ411x7h7")

    assert recorded, "download_audio 没有调用 YoutubeDL，测试失去意义"
    for params in recorded:
        assert params.get("no_color") is True, f"下载路径未关颜色：{params}"


def test_subtitle_probe_disables_color(monkeypatch):
    from app.services import bili_player_api, subtitles

    recorded = _record_youtubedl_params(monkeypatch)
    # 第 1 层失败后会走 dm/view 的 AI 字幕接口。这条测试只关心 yt-dlp 的
    # 参数，不该真的发网络请求 → 把第 2 层也打桩成「无字幕」。
    monkeypatch.setattr(bili_player_api, "get_subtitle_tracks", lambda bvid: [])

    assert subtitles.try_extract_subtitle_transcript("https://www.bilibili.com/video/BV1GJ411x7h7") is None

    assert recorded, "字幕探测没有调用 YoutubeDL，测试失去意义"
    for params in recorded:
        assert params.get("no_color") is True, f"字幕路径未关颜色：{params}"


def test_download_error_never_leaks_color_codes_to_message(monkeypatch):
    """端到端：yt-dlp 抛带颜色的异常 → 最终 error.message 不含 ANSI。"""
    from app.services import audio

    _record_youtubedl_params(monkeypatch)
    with pytest.raises(AppException) as excinfo:
        audio.download_audio("https://www.bilibili.com/video/BV1GJ411x7h7")

    message = excinfo.value.message
    assert "\x1b" not in message
    assert "HTTP Error 412" in message

