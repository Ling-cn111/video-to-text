"""测活端点测试（POST /api/byok/test）：结果映射、安全边界与契约形状。

与 test_byok.py 的分工：那里验 summarize 的 BYOK 链路，这里只验「测活」——
用桩 httpx.post 模拟服务商的各状态码，断言收敛为 {ok, code, message}（HTTP 200）
与校验类失败的标准 400 形状；Key 绝不出现在响应体中。
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import byok as byok_module
from app.services import summarizer as summarizer_module

client = TestClient(app)

VALID_BODY = {"llmBaseUrl": "https://api.example.com/v1", "llmModel": "test-model"}
KEY_HEADER = {"X-Vtt-Llm-Key": "user-test-key"}


@pytest.fixture(autouse=True)
def _bypass_dns_gate(monkeypatch):
    """测活单测不依赖真实 DNS：关闭解析复检（字面量 SSRF 检查仍在，SSRF 用例另有覆盖）。"""
    monkeypatch.setattr(byok_module, "resolves_to_blocked_ip", lambda host: False)


def _stub_status(monkeypatch, status: int, message: str = "err"):
    """打桩 httpx.post 返回指定状态码（服务商模拟）。"""

    def fake_post(url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(status, request=request, json={"error": {"message": message}})

    monkeypatch.setattr(summarizer_module.httpx, "post", fake_post)


def test_test_endpoint_success_roundtrip(monkeypatch):
    """200 + choices → {ok:True, model, latencyMs}；请求体带极短 max_tokens 与用户 Key。"""
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "pong"}}], "model": "served-model"},
        )

    monkeypatch.setattr(summarizer_module.httpx, "post", fake_post)
    response = client.post("/api/byok/test", json=VALID_BODY, headers=KEY_HEADER)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["model"] == "served-model"
    assert body["latencyMs"] >= 0
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["json"]["max_tokens"] == 1
    assert captured["headers"]["Authorization"] == "Bearer user-test-key"


@pytest.mark.parametrize(
    ("status", "code", "message_fragment"),
    [
        (401, "BYOK_KEY_INVALID", "被服务商拒绝"),
        (403, "BYOK_KEY_INVALID", "被服务商拒绝"),
        (404, "BYOK_ENDPOINT_INVALID", "模型不存在"),
        (402, "SUMMARIZE_FAILED", "余额不足"),
        (429, "RATE_LIMITED", "限流"),
        (500, "SUMMARIZE_FAILED", "AI 服务返回错误"),
    ],
)
def test_test_endpoint_maps_provider_status(monkeypatch, status, code, message_fragment):
    """服务商各状态码收敛为 HTTP 200 + {ok:False, code, message}，不再向上抛 5xx。"""
    _stub_status(monkeypatch, status)
    response = client.post("/api/byok/test", json=VALID_BODY, headers=KEY_HEADER)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["code"] == code
    assert message_fragment in body["message"]
    assert body["latencyMs"] >= 0


def test_test_endpoint_provider_detail_appended(monkeypatch):
    """服务商错误体里的 message 被截断拼进提示，便于用户判断（不含 Key）。"""
    _stub_status(monkeypatch, 400, "model not found for tenant")
    response = client.post("/api/byok/test", json=VALID_BODY, headers=KEY_HEADER)
    body = response.json()
    assert body["ok"] is False
    assert "model not found for tenant" in body["message"]


def test_test_endpoint_network_failure(monkeypatch):
    """连接失败 → ok:False + 连接提示。"""

    def fake_post(url, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(summarizer_module.httpx, "post", fake_post)
    response = client.post("/api/byok/test", json=VALID_BODY, headers=KEY_HEADER)
    body = response.json()
    assert body["ok"] is False
    assert "无法连接服务商" in body["message"]


def test_test_endpoint_malformed_200(monkeypatch):
    """200 但没有 choices → 提示确认模型支持 chat/completions。"""

    def fake_post(url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(200, request=request, json={"unexpected": True})

    monkeypatch.setattr(summarizer_module.httpx, "post", fake_post)
    response = client.post("/api/byok/test", json=VALID_BODY, headers=KEY_HEADER)
    body = response.json()
    assert body["ok"] is False
    assert "chat/completions" in body["message"]


def test_test_endpoint_never_echoes_key(monkeypatch):
    """安全边界：无论成功失败，响应体里绝不出现用户 Key。"""
    _stub_status(monkeypatch, 401)
    response = client.post("/api/byok/test", json=VALID_BODY, headers=KEY_HEADER)
    assert "user-test-key" not in response.text

    def fake_ok(url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(200, request=request, json={"choices": [{"message": {"content": "pong"}}]})

    monkeypatch.setattr(summarizer_module.httpx, "post", fake_ok)
    response = client.post("/api/byok/test", json=VALID_BODY, headers=KEY_HEADER)
    assert "user-test-key" not in response.text


def test_test_endpoint_requires_all_three_fields():
    """缺地址 / 缺模型 / 缺 Key：标准 400 错误形状，动作指向面板填写。"""
    cases = [
        ({"llmBaseUrl": "", "llmModel": "m"}, "接口地址"),
        ({"llmBaseUrl": "https://api.example.com/v1", "llmModel": ""}, "模型名"),
    ]
    for body, fragment in cases:
        response = client.post("/api/byok/test", json=body, headers=KEY_HEADER)
        assert response.status_code == 400
        assert fragment in response.json()["error"]["message"]

    response = client.post("/api/byok/test", json=VALID_BODY)  # 无 Key 请求头
    assert response.status_code == 400
    assert "API Key" in response.json()["error"]["message"]


def test_test_endpoint_rejects_ssrf_like_url():
    """SSRF 校验与 summarize 同源：内网地址直接 400 BYOK_ENDPOINT_INVALID。"""
    response = client.post(
        "/api/byok/test",
        json={"llmBaseUrl": "http://127.0.0.1:8000/v1", "llmModel": "m"},
        headers=KEY_HEADER,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BYOK_ENDPOINT_INVALID"
