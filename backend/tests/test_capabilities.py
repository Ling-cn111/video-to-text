"""能力探测端点测试（任务 M2-P2-A）：两态返回 + 零敏感信息边界。"""
from fastapi.testclient import TestClient

import app.routers.capabilities as capabilities_module
from app.main import app

client = TestClient(app)

_ALL_KEYS = {"cloudAsrConfigured", "summarizeConfigured", "localAsrAvailable", "fileUploadConfigured"}


def test_capabilities_all_configured(monkeypatch):
    monkeypatch.setattr(capabilities_module, "CLOUD_ASR_API_KEY", "sk-cloud")
    monkeypatch.setattr(capabilities_module, "DEEPSEEK_API_KEY", "sk-llm")
    monkeypatch.setattr(capabilities_module, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(capabilities_module, "_local_asr_available", lambda: False)
    monkeypatch.setattr(capabilities_module.storage, "is_file_storage_configured", lambda: True)

    response = client.get("/api/capabilities")
    assert response.status_code == 200
    assert response.json() == {
        "cloudAsrConfigured": True,
        "summarizeConfigured": True,
        "localAsrAvailable": False,
        "fileUploadConfigured": True,
    }


def test_capabilities_none_configured(monkeypatch):
    monkeypatch.setattr(capabilities_module, "CLOUD_ASR_API_KEY", "")
    monkeypatch.setattr(capabilities_module, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(capabilities_module, "DASHSCOPE_API_KEY", "")
    monkeypatch.setattr(capabilities_module, "_local_asr_available", lambda: False)
    monkeypatch.setattr(capabilities_module.storage, "is_file_storage_configured", lambda: False)

    response = client.get("/api/capabilities")
    assert response.status_code == 200
    assert response.json() == {
        "cloudAsrConfigured": False,
        "summarizeConfigured": False,
        "localAsrAvailable": False,
        "fileUploadConfigured": False,
    }


def test_capabilities_file_upload_reflects_oss_configuration(monkeypatch):
    """fileUploadConfigured 是 OSS 配置探测：四项齐备才 true（M3）。

    本地开发未配 OSS → false，前端据此隐藏「上传本地视频」入口。
    """
    monkeypatch.setattr(capabilities_module.storage, "is_file_storage_configured", lambda: False)
    assert client.get("/api/capabilities").json()["fileUploadConfigured"] is False

    monkeypatch.setattr(capabilities_module.storage, "is_file_storage_configured", lambda: True)
    assert client.get("/api/capabilities").json()["fileUploadConfigured"] is True


def test_capabilities_local_asr_probe_reflects_dependency(monkeypatch):
    """localAsrAvailable 是依赖可用性探测：本机装没装 faster-whisper 决定真假。

    线上 FC 镜像不含该包 → false，前端据此隐藏「本地 / 云端」选择器。
    """
    monkeypatch.setattr(capabilities_module, "_local_asr_available", lambda: True)
    assert client.get("/api/capabilities").json()["localAsrAvailable"] is True

    monkeypatch.setattr(capabilities_module, "_local_asr_available", lambda: False)
    assert client.get("/api/capabilities").json()["localAsrAvailable"] is False


def test_capabilities_qwen_provider_uses_dashscope_key(monkeypatch):
    monkeypatch.setattr(capabilities_module, "LLM_PROVIDER", "qwen")
    monkeypatch.setattr(capabilities_module, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(capabilities_module, "DASHSCOPE_API_KEY", "sk-qwen")

    assert client.get("/api/capabilities").json()["summarizeConfigured"] is True


def test_capabilities_response_contains_no_key_traces(monkeypatch):
    """安全边界（M2-P2-A 硬约束）：响应键集恰为布尔字段，任何 Key 痕迹都不允许出现。"""
    sentinel = "sk-SENTINEL-SECRET-VALUE"
    monkeypatch.setattr(capabilities_module, "CLOUD_ASR_API_KEY", sentinel)
    monkeypatch.setattr(capabilities_module, "DEEPSEEK_API_KEY", sentinel)

    raw = client.get("/api/capabilities").text
    assert set(client.get("/api/capabilities").json().keys()) == _ALL_KEYS
    assert sentinel not in raw  # 完整 Key 不出现
    assert "sk-" not in raw  # 前缀不出现
    assert "SENTINEL" not in raw  # 任何片段不出现
