"""URL 安全守卫（与前端 src/lib/url-guard.ts 同一套规则）。

约束：仅允许 http/https，拒绝 localhost、环回、私有与保留地址；
平台白名单再过滤一层，确保 yt-dlp 只会拿到受支持站点的公开 URL。
"""
import ipaddress
import socket
from urllib.parse import ParseResult, urlparse

from app.errors import InvalidUrlError

_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal")

# RFC 6598 共享地址空间（100.64.0.0/10）。`ipaddress` 不把它算进 is_private，
# 但云厂商的 metadata / 内部服务常落在这段（阿里云 metadata 就是 100.100.100.200），
# 所以必须单独拦。
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")


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
        or ip in _SHARED_ADDRESS_SPACE
    )


def is_blocked_host(hostname: str | None) -> bool:
    """字面量判断：域名后缀 + IP 字面量。**不做 DNS 解析**。

    只靠这一层挡不住「域名解析到内网」的绕过（`127.0.0.1.nip.io` 之类）——
    需要防住这种绕过的调用方，必须再用 `resolves_to_blocked_ip()` 复检。
    """
    if not hostname:
        return True
    host = hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
        return True
    return _is_reserved_ip(host)


def resolves_to_blocked_ip(hostname: str | None) -> bool:
    """解析 DNS 后逐个 IP 复检，挡住「域名解析到内网」的 SSRF 绕过。

    为什么必须有这一层：`is_blocked_host()` 只看字符串。攻击者注册一个
    `127.0.0.1.nip.io`（或任何把 A 记录指向内网的域名）就能绕过字面量检查，
    让后端替他请求本机或内网服务。只有真解析一次、把每个结果都过一遍
    `_is_reserved_ip()`，才能挡住这类域名。

    返回 True 表示「不安全」：解析失败、解析为空、或任一结果命中保留地址。
    解析失败按不安全处理——连不上和连到内网，宁可直接拒。

    注意这是「先检查后连接」，DNS 重绑定（TOCTOU）理论上仍可绕过：检查时解析到
    公网 IP、真正连接时解析到内网。彻底防御需要固定解析结果再连接（如自定义
    httpx transport），成本远超本次范围；这里先把「静态指向内网的域名」堵死。
    """
    if not hostname:
        return True
    try:
        # getaddrinfo 会同时返回 A 与 AAAA；取全部结果逐个判，任一命中即拒
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return True
    if not infos:
        return True
    for info in infos:
        address = info[4][0]
        if _is_reserved_ip(address):
            return True
    return False


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
