"""yt-dlp 选项的单一来源：所有调用点都从这里取公共选项。

为什么集中：`parser` / `audio`（2 处）/ `subtitles` 各有自己的选项字典，
以前各写一份，改一处漏三处。已经踩过的两个坑都是这样产生的：

1. **ANSI 颜色码**：漏设 `no_color` 时，yt-dlp 的报错会带 `\\\\x1b[0;31m`，
   被拼进 `error.message` 后前端显示乱码。
2. **机房 IP 风控**：阿里云出口 IP 请求 B站常报 `HTTP 412 Precondition Failed`，
   需要带上 Referer / Accept-Language / 设备指纹 Cookie，必要时再加账号 Cookie。

公共选项只管「与任务无关的通用行为」，各调用点再按需覆盖自己的
format / outtmpl / extract_flat 等。

注意：`base_options()` **每次调用都会重新构建**，因为它包含动态内容
（设备指纹可能需要触发一次网络请求、配置可能在运行时被改）。所有调用点
都必须在**使用时**调用它，不能像以前那样在模块导入时算一次就固化下来
——否则冷启动时取到的指纹会一直用到进程结束。
"""
from app import config
from app.services import bili_fingerprint


def cookie_header() -> str:
    """拼出完整的 Cookie 头：账号 Cookie（若有）+ 设备指纹。

    - `BILI_COOKIE`：用户配置的浏览器整条 Cookie，优先级最高（已登录 = 最可信）
    - 设备指纹：未配置账号 Cookie 时自动补 buvid3/buvid4，同样能显著降低 412

    没有可用内容时返回空字符串。
    """
    parts: list[str] = []
    if config.BILI_COOKIE:
        parts.append(config.BILI_COOKIE)
    else:
        # 只有没配账号 Cookie 时才补指纹：已登录用户的 Cookie 里通常已含
        # buvid3，混入另一个设备指纹反而可能被判定为「Cookie 与设备不一致」。
        device = bili_fingerprint.device_cookie()
        if device:
            parts.append(device)
    return "; ".join(parts)


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

    # ---- 风控缓解 ----
    headers: dict[str, str] = {}
    if config.BILI_USER_AGENT:
        headers["User-Agent"] = config.BILI_USER_AGENT
    # Referer 对 B站 CDN 是硬性要求；带上不会伤害其他平台
    headers["Referer"] = "https://www.bilibili.com/"
    headers["Accept-Language"] = "zh-CN,zh;q=0.9,en;q=0.8"

    # Cookie 头：账号 Cookie 或自动获取的设备指纹（见 cookie_header）
    cookie = cookie_header()
    if cookie:
        headers["Cookie"] = cookie

    options["http_headers"] = headers
    return options
