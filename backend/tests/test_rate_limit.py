"""请求限流测试（令牌桶）：正常通过 / 超限 429 / 时间窗回填 / IP 隔离 / 禁用开关 / HTTP 层。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app as main_app
from app.services.rate_limit import RateLimitMiddleware, TokenBucketLimiter


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


def _mini_app(per_minute: int) -> FastAPI:
    """独立小应用（不复用 main 的全局限流），带一个恒 200 的 POST 探针。"""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limiter=TokenBucketLimiter(per_minute))

    @app.post("/probe")
    def probe():
        return {"ok": True}

    return app


def test_http_429_with_contract_shape():
    client = TestClient(_mini_app(per_minute=2))
    assert client.post("/probe").status_code == 200
    assert client.post("/probe").status_code == 200
    limited = client.post("/probe")
    assert limited.status_code == 429
    body = limited.json()
    assert body["error"]["code"] == "RATE_LIMITED"
    assert "请求太频繁" in body["error"]["message"]


def test_http_get_not_limited():
    """GET 轮询不限流：只有 POST 计数。"""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limiter=TokenBucketLimiter(per_minute=1))

    @app.post("/probe")
    def probe():
        return {"ok": True}

    @app.get("/probe")
    def probe_get():
        return {"ok": True}

    client = TestClient(_mini_app(1)) if False else TestClient(app)
    assert client.post("/probe").status_code == 200
    assert client.post("/probe").status_code == 429
    for _ in range(20):
        assert client.get("/probe").status_code == 200  # GET 不受影响


def test_forwarded_for_header_isolation():
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limiter=TokenBucketLimiter(per_minute=1))

    @app.post("/probe")
    def probe():
        return {"ok": True}

    client = TestClient(app)
    assert client.post("/probe", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 200
    assert client.post("/probe", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 429
    assert client.post("/probe", headers={"X-Forwarded-For": "5.6.7.8"}).status_code == 200


def test_main_app_rate_limiter_can_be_disabled_by_tests():
    """conftest 禁用主应用限流后，main app 的 POST 不受每分钟 10 次约束。"""
    client = TestClient(main_app)
    for _ in range(12):
        # 无效 URL 也会被路由校验拒绝（422），但不应出现 429
        response = client.post("/api/parse", json={"url": "not-a-url"})
        assert response.status_code in (400, 422)
