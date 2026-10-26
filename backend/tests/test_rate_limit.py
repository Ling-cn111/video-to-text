"""请求限流测试（令牌桶）：正常通过 / 超限 429 / 时间窗回填 / IP 隔离 / 禁用开关 / HTTP 层。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app as main_app
from app.services import rate_limit as rate_limit_module
from app.services.rate_limit import MemoryRateLimiter, RateLimitMiddleware, TokenBucketLimiter


def test_normal_requests_pass():
    limiter = TokenBucketLimiter(per_minute=10)
    assert all(limiter.allow("ip1") for _ in range(10))


def test_over_limit_rejected():
    limiter = TokenBucketLimiter(per_minute=2)
    assert limiter.allow("ip1")
    assert limiter.allow("ip1")
    assert not limiter.allow("ip1")  # 第 3 次超限
    assert limiter.allow("ip2")  # IP 隔离：其他 IP 不受影响


def test_time_window_refills_tokens(monkeypatch):
    """时间窗回填：推进 60 秒后桶恢复满额。"""
    limiter = TokenBucketLimiter(per_minute=2)
    clock = {"now": 1000.0}
    assert limiter.allow("ip1", now=clock["now"])
    assert limiter.allow("ip1", now=clock["now"])
    assert not limiter.allow("ip1", now=clock["now"])
    clock["now"] += 60.0  # 一个整窗口：回填 2 个令牌
    assert limiter.allow("ip1", now=clock["now"])
    assert limiter.allow("ip1", now=clock["now"])
    assert not limiter.allow("ip1", now=clock["now"])


def test_disabled_when_per_minute_zero():
    limiter = TokenBucketLimiter(per_minute=0)
    assert all(limiter.allow("ip1") for _ in range(1000))


def _mini_app(per_minute: int, global_per_minute: int = 0) -> FastAPI:
    """独立小应用（不复用 main 的全局限流），带一个恒 200 的 POST 探针。"""
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        limiter=MemoryRateLimiter(per_minute),
        global_limiter=MemoryRateLimiter(global_per_minute),
    )

    @app.post("/probe")
    def probe():
        return {"ok": True}

    return app


# 真实 IP 作为 TCP 对端：TestClient 默认 client=("testclient", 50000) 不是合法 IP，
# 会被判定为「标识不可信」而跳过每客户端配额（见 _should_limit_per_client）。
_CLIENT = ("8.8.8.8", 12345)


def _client(app: FastAPI, ip: str = _CLIENT[0]) -> TestClient:
    return TestClient(app, client=(ip, 12345))


def test_http_429_with_contract_shape():
    client = _client(_mini_app(per_minute=2))
    assert client.post("/probe").status_code == 200
    assert client.post("/probe").status_code == 200
    limited = client.post("/probe")
    assert limited.status_code == 429
    body = limited.json()
    assert body["error"]["code"] == "RATE_LIMITED"
    assert "请求太频繁" in body["error"]["message"]


def test_http_429_carries_retry_after():
    """429 必须带 Retry-After：前端冷却时长据此对齐，避免冷却结束后点必然再 429。"""
    client = _client(_mini_app(per_minute=1))
    assert client.post("/probe").status_code == 200
    limited = client.post("/probe")
    assert limited.status_code == 429
    # per_minute=1 → 补满 1 个令牌需 60 秒
    assert limited.headers["retry-after"] == "60"


def test_http_get_not_limited():
    """GET 轮询不限流：只有 POST 计数。"""
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        limiter=MemoryRateLimiter(per_minute=1),
        global_limiter=MemoryRateLimiter(0),
    )

    @app.post("/probe")
    def probe():
        return {"ok": True}

    @app.get("/probe")
    def probe_get():
        return {"ok": True}

    client = _client(app)
    assert client.post("/probe").status_code == 200
    assert client.post("/probe").status_code == 429
    for _ in range(20):
        assert client.get("/probe").status_code == 200  # GET 不受影响


def test_forwarded_for_ignored_when_no_trusted_proxy():
    """安全默认：未配置 TRUSTED_PROXY_CIDRS 时完全忽略 X-Forwarded-For。

    这是 S3 漏洞的核心修复点——旧实现取 XFF 首段，攻击者每次换一个伪造 IP
    就能无限刷（实测轮换 14 个伪造 IP 得到 14 次放行、零 429）。
    """
    app = _mini_app(per_minute=1)
    client = _client(app)
    assert client.post("/probe", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 200
    # 换一个伪造 IP 不再换来新桶：仍然受限
    assert client.post("/probe", headers={"X-Forwarded-For": "5.6.7.8"}).status_code == 429
    assert client.post("/probe", headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 429


def test_forwarded_for_parsed_when_peer_is_trusted(monkeypatch):
    """对端可信时才解析 XFF，并从右往左取第一个不可信地址（标准算法）。

    攻击者能控制的只有链的最左边，所以「最左端」是错误答案，「最右不可信」才对。
    """
    from app.services import rate_limit as rate_limit_module

    monkeypatch.setattr(
        rate_limit_module, "_TRUSTED_NETWORKS", (rate_limit_module.ipaddress.ip_network("10.0.0.0/8"),)
    )
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"1.1.1.1, 10.0.0.5, 10.0.0.6")],
        "client": ("10.0.0.9", 1234),
    }
    # 从右往左跳过可信的 10.x，第一个不可信是 1.1.1.1
    assert rate_limit_module._client_ip(scope) == "1.1.1.1"

    # 攻击者在最左边塞伪造值：右向解析后它被忽略，取到的仍是真实那一跳
    spoofed = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"9.9.9.9, 1.1.1.1, 10.0.0.6")],
        "client": ("10.0.0.9", 1234),
    }
    assert rate_limit_module._client_ip(spoofed) == "1.1.1.1"


def test_forwarded_for_ignored_when_peer_untrusted(monkeypatch):
    """对端不可信时即便配了可信网段，也不采信 XFF（伪造头永远无效）。"""
    from app.services import rate_limit as rate_limit_module

    monkeypatch.setattr(
        rate_limit_module, "_TRUSTED_NETWORKS", (rate_limit_module.ipaddress.ip_network("10.0.0.0/8"),)
    )
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"1.2.3.4")],
        "client": ("1.1.1.1", 5555),
    }
    assert rate_limit_module._client_ip(scope) == "1.1.1.1"


def test_ipv6_bucket_merges_per_64():
    """IPv6 按 /64 归并：同一 /64 内换地址不再换来新桶。"""
    from app.services import rate_limit as rate_limit_module

    a = rate_limit_module._client_ip({"type": "http", "headers": [], "client": ("2400:3200:1:2::1", 1)})
    b = rate_limit_module._client_ip({"type": "http", "headers": [], "client": ("2400:3200:1:2::ffff", 2)})
    assert a == b == "2400:3200:1:2::/64"

    # 不同 /64 才是不同桶
    c = rate_limit_module._client_ip({"type": "http", "headers": [], "client": ("2400:3200:1:3::1", 3)})
    assert c != a


def test_client_ip_normalizes_mapped_and_port_forms():
    """::ffff:1.2.3.4 与 1.2.3.4 必须是同一个桶；带端口的写法要能剥掉。"""
    from app.services import rate_limit as rate_limit_module

    plain = rate_limit_module._client_ip({"type": "http", "headers": [], "client": ("1.2.3.4", 1)})
    mapped = rate_limit_module._client_ip(
        {"type": "http", "headers": [], "client": ("::ffff:1.2.3.4", 2)}
    )
    assert plain == mapped == "1.2.3.4"
    assert rate_limit_module._parse_ip("1.2.3.4:5678") is not None
    assert rate_limit_module._parse_ip("[::1]:8080") is not None


def test_infra_peer_disables_per_client_quota():
    """网关内网地址无法区分用户：跳过每客户端配额，避免全站用户挤一个桶。

    S3 实测复现：浏览器不发 XFF 时，Serverless 网关给出的对端是固定内网地址，
    旧实现拿它当桶 key → 整站共享 10 次/分钟，一人刷满全员 429。
    """
    from app.services import rate_limit as rate_limit_module

    for infra in ("10.0.0.9", "172.16.5.4", "192.168.1.10", "127.0.0.1", "100.64.3.2"):
        scope = {"type": "http", "headers": [], "client": (infra, 1234)}
        _, per_client = rate_limit_module._should_limit_per_client(scope)
        assert per_client is False, infra

    # 公网地址照常按客户端限流
    scope = {"type": "http", "headers": [], "client": ("8.8.8.8", 1234)}
    _, per_client = rate_limit_module._should_limit_per_client(scope)
    assert per_client is True


def test_infra_peer_requests_still_pass_per_client_gate():
    """内网对端不会被每客户端配额误伤：连发多次全部放行（只受全站配额约束）。"""
    app = _mini_app(per_minute=1, global_per_minute=100)
    client = _client(app, ip="10.0.0.9")
    for _ in range(10):
        assert client.post("/probe").status_code == 200


def test_global_quota_stops_rotating_clients():
    """全站配额：客户端标识再换也逃不掉总量上限。

    模拟「IP 可伪造」的最坏情况——每次请求都是新客户端，每客户端配额永不触发，
    此时只有全局桶能拦住刷量。
    """
    app = _mini_app(per_minute=100, global_per_minute=3)
    for index in range(3):
        assert _client(app, ip=f"8.8.8.{index}").post("/probe").status_code == 200
    # 第 4 次即便换新 IP 也被全站配额拦下
    assert _client(app, ip="9.9.9.9").post("/probe").status_code == 429


def test_main_app_rate_limiter_can_be_disabled_by_tests():
    """conftest 禁用主应用限流后，main app 的 POST 不受每分钟 10 次约束。"""
    client = TestClient(main_app)
    for _ in range(12):
        # 无效 URL 也会被路由校验拒绝（422），但不应出现 429
        response = client.post("/api/parse", json={"url": "not-a-url"})
        assert response.status_code in (400, 422)


def test_429_response_carries_cors_headers(monkeypatch):
    """429 必须带 CORS 头，否则跨源前端只会看到 CORS 错误而非「请求太频繁」。

    中间件顺序是这里的命门：Starlette 的 `add_middleware` 让**后注册的包在外层**。
    旧写法先加 CORS 再加限流，限流器位于最外层，它直接返回的 429 绕过了 CORS 处理，
    浏览器拿不到 `access-control-allow-origin` 就把响应判为跨源失败——前端显示
    「网络异常」，用户完全看不到限流提示（实测复现）。必须「先限流、后 CORS」。
    """
    monkeypatch.setattr(rate_limit_module._LIMITER, "per_minute", 1)
    monkeypatch.setattr(rate_limit_module._GLOBAL_LIMITER, "per_minute", 0)

    client = TestClient(main_app, client=("8.8.8.8", 12345))
    origin = {"Origin": "http://localhost:3000"}
    assert client.post("/api/parse", json={"url": "bad"}, headers=origin).status_code == 400
    limited = client.post("/api/parse", json={"url": "bad"}, headers=origin)

    assert limited.status_code == 429
    # 关键断言：限流响应同样经过 CORS 处理
    assert limited.headers["access-control-allow-origin"] == "http://localhost:3000"
    # 且 Retry-After 对前端 JS 可见（否则 useRateLimitCooldown 读不到，只能退回兜底值）
    assert "Retry-After" in limited.headers["access-control-expose-headers"]
