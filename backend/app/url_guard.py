"""URL 安全守卫（与前端 src/lib/url-guard.ts 同一套规则）。

约束：仅允许 http/https，拒绝 localhost、环回、私有与保留地址；
平台白名单再过滤一层，确保 yt-dlp 只会拿到受支持站点的公开 URL。
"""
import ipaddress
from urllib.parse import ParseResult, urlparse

from app.errors import InvalidUrlError

_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal")


def _is_reserved_ip(host: str) -> bool:
    """host 是 IP 字面量时，判断是否属于保留/私有/环回等不可公网访问的地址。"""
    candidate = host.strip("[]")
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        return False

    # IPv4-mapped IPv6（如 ::ffff:127.0.0.1）按映射后的 IPv4 判定
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped

    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def is_blocked_host(hostname: str | None) -> bool:
    if not hostname:
        return True
    host = hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
        return True
    return _is_reserved_ip(host)


def validate_public_http_url(raw_url: str) -> ParseResult:
    """校验用户输入的 URL：仅 http/https 且 host 非保留地址。

    校验失败抛 InvalidUrlError；通过后仍需过平台白名单再发起任何请求。
    """
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in ("http", "https"):
        raise InvalidUrlError("链接格式不正确，请粘贴完整的视频页面链接")
    if is_blocked_host(parsed.hostname):
        raise InvalidUrlError("该链接地址不可访问")
    return parsed
