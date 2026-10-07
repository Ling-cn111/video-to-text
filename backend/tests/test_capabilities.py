"""能力探测端点测试（任务 M2-P2-A）：两态返回 + 零敏感信息边界。"""
from fastapi.testclient import TestClient

import app.routers.capabilities as capabilities_module
from app.main import app

client = TestClient(app)


def test_capabilities_all_configured(monkeypatch):
    monkeypatch.setattr(capabilities_module, "CLOUD_ASR_API_KEY", "sk-cloud")
    monkeypatch.setattr(capabilities_module, "DEEPSEEK_API_KEY", "sk-llm")
    monkeypatch.setattr(capabilities_module, "LLM_PROVIDER", "deepseek")

    response = client.get("/api/capabilities")
    assert response.status_code == 200
    assert response.json() == {"cloudAsrConfigured": True, "summarizeConfigured": True}


def test_capabilities_none_configured(monkeypatch):
    monkeypatch.setattr(capabilities_module, "CLOUD_ASR_API_KEY", "")
    monkeypatch.setattr(capabilities_module, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(capabilities_module, "DASHSCOPE_API_KEY", "")

    response = client.get("/api/capabilities")
    assert response.status_code == 200
    assert response.json() == {"cloudAsrConfigured": False, "summarizeConfigured": False}


def test_capabilities_qwen_provider_uses_dashscope_key(monkeypatch):
    monkeypatch.setattr(capabilities_module, "LLM_PROVIDER", "qwen")
    monkeypatch.setattr(capabilities_module, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(capabilities_module, "DASHSCOPE_API_KEY", "sk-qwen")

    assert client.get("/api/capabilities").json()["summarizeConfigured"] is True


def test_capabilities_response_contains_no_key_traces(monkeypatch):
    """安全边界（M2-P2-A 硬约束）：响应键集恰为两个布尔，任何 Key 痕迹都不允许出现。"""
    sentinel = "sk-SENTINEL-SECRET-VALUE"
    monkeypatch.setattr(capabilities_module, "CLOUD_ASR_API_KEY", sentinel)
    monkeypatch.setattr(capabilities_module, "DEEPSEEK_API_KEY", sentinel)

    raw = client.get("/api/capabilities").text
    assert set(client.get("/api/capabilities").json().keys()) == {"cloudAsrConfigured", "summarizeConfigured"}
    assert sentinel not in raw  # 完整 Key 不出现
    assert "sk-" not in raw  # 前缀不出现
    assert "SENTINEL" not in raw  # 任何片段不出现
