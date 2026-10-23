"""yt-dlp 选项的单一来源：所有调用点都从这里取公共选项。

为什么集中：`parser` / `audio`（2 处）/ `subtitles` 各有自己的选项字典，
以前各写一份，改一处漏三处。已经踩过的两个坑都是这样产生的：

1. **ANSI 颜色码**：漏设 `no_color` 时，yt-dlp 的报错会带 `\\x1b[0;31m`，
   被拼进 `error.message` 后前端显示乱码。
2. **机房 IP 风控**：阿里云出口 IP 请求 B站常报 `HTTP 412 Precondition Failed`，
   需要带上与真实浏览器一致的 Referer / Accept-Language，必要时配 Cookie。

公共选项只管「与任务无关的通用行为」，各调用点再按需覆盖自己的
format / outtmpl / extract_flat 等。
"""
from app import config


def base_options() -> dict:
    """所有 yt-dlp 调用都应带上的公共选项。

    返回新字典，调用方可安全地 `{**base_options(), ...}` 覆盖。
    """
    options: dict = {
        # ---- 输出与稳定性 ----
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # 报错文本会进 error.message，不能让终端颜色码漏给前端
        "no_color": True,
        "socket_timeout": config.EXTRACT_TIMEOUT_SECONDS,
    }

    # ---- 风控缓解（默认不改变行为，只有显式配置时才注入）----
    headers: dict[str, str] = {}
    if config.BILI_USER_AGENT:
        headers["User-Agent"] = config.BILI_USER_AGENT
    # Referer 对 B站 CDN 是硬性要求；带上不会伤害其他平台
    headers.setdefault("Referer", "https://www.bilibili.com/")
    headers.setdefault("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
    options["http_headers"] = headers

    # Cookie 只在配置了才注入：含账号凭证，绝不硬编码
    if config.BILI_COOKIE:
        options["http_headers"]["Cookie"] = config.BILI_COOKIE

    return options
