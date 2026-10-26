"""用户自带 Key（BYOK）测试：请求头解析、Key 覆盖、鉴权失败分流、不泄露 Key。"""
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app.services.asr as asr_module
import app.services.summarizer as summarizer_module
from app.main import app
from app.services.byok import (
    HEADER_ASR_KEY,
    HEADER_LLM_KEY,
    MAX_KEY_LENGTH,
    is_auth_failure,
    key_source,
    normalize_key,
)

client = TestClient(app)

VIDEO_ID = "BV1xx411c7mD"
TRANSCRIPT = [{"time": 0.0, "text": "第一句。"}, {"time": 30.0, "text": "第二句。"}]
VALID_PAYLOAD = {
    "summary": "测试用概要。",
    "keyPoints": [{"time": 0.0, "text": "要点一。"}],
    "chapters": [{"title": "开场", "timeStart": 0.0, "timeEnd": 30.0, "note": "说明。"}],
}


# ---- normalize_key ----

def test_normalize_key_strips_whitespace():
    assert normalize_key("  sk-abc  ") == "sk-abc"


def test_normalize_key_empty_is_none():
    assert normalize_key(None) is None
    assert normalize_key("") is None
    assert normalize_key("   ") is None


def test_normalize_key_rejects_oversized():
    # 超长值不可能是正常请求，丢弃而不是截断（截断后是一把错误的 Key，报错更难懂）
    assert normalize_key("k" * (MAX_KEY_LENGTH + 1)) is None


def test_normalize_key_keeps_boundary_length():
    boundary = "k" * MAX_KEY_LENGTH
    assert normalize_key(boundary) == boundary


# ---- key_source / is_auth_failure ----

def test_key_source_never_returns_key_content():
    assert key_source("sk-secret", "sk-server") == "<byok>"
    assert key_source(None, "sk-server") == "<server>"
    assert key_source(None, "") == "<none>"


def test_is_auth_failure_only_for_401_403():
    assert is_auth_failure(401)
    assert is_auth_failure(403)
    assert not is_auth_failure(500)
    assert not is_auth_failure(429)


# ---- 总结：请求级 Key 覆盖 ----

def _mock_chat_capture(monkeypatch, captured: dict):
    def fake_chat(base_url, api_key, model, user_content, detail=None, from_user=False):
        captured["api_key"] = api_key
        captured["from_user"] = from_user
        return json.dumps(VALID_PAYLOAD, ensure_ascii=False), {
            "prompt_tokens": 10,
            "completion_tokens": 10,
        }

    monkeypatch.setattr(summarizer_module, "_chat", fake_chat)


def test_summarize_uses_byok_key_when_header_present(monkeypatch):
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "site-key")
    captured: dict = {}
    _mock_chat_capture(monkeypatch, captured)

    response = client.post(
        "/api/summarize",
        json={"transcript": TRANSCRIPT, "title": "标题", "duration": 60},
        headers={HEADER_LLM_KEY: "user-key"},
    )
    assert response.status_code == 200
    assert captured["api_key"] == "user-key"
    assert captured["from_user"] is True


def test_summarize_falls_back_to_site_key_without_header(monkeypatch):
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "site-key")
    captured: dict = {}
    _mock_chat_capture(monkeypatch, captured)

    response = client.post("/api/summarize", json={"transcript": TRANSCRIPT, "title": "标题", "duration": 60})
    assert response.status_code == 200
    assert captured["api_key"] == "site-key"
    assert captured["from_user"] is False


def test_summarize_byok_works_without_site_key(monkeypatch):
    """站点没配 Key 时，用户自带的 Key 仍然要能跑通——这正是 BYOK 的意义。"""
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(summarizer_module, "DASHSCOPE_API_KEY", "")
    captured: dict = {}
    _mock_chat_capture(monkeypatch, captured)

    response = client.post(
        "/api/summarize",
        json={"transcript": TRANSCRIPT, "title": "标题", "duration": 60},
        headers={HEADER_LLM_KEY: "user-key"},
    )
    assert response.status_code == 200
    assert captured["api_key"] == "user-key"


def test_summarize_without_any_key_reports_not_configured(monkeypatch):
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(summarizer_module, "DASHSCOPE_API_KEY", "")

    response = client.post("/api/summarize", json={"transcript": TRANSCRIPT, "title": "标题", "duration": 60})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SUMMARIZE_NOT_CONFIGURED"


def _fail_http_with_status(monkeypatch, status: int):
    """打桩 httpx.post 返回 401/403。

    注意必须打在 `_chat` 内部的 httpx.post 上，而不是替换 `_chat` 本身：
    `_auth_error` 的分流逻辑就在 `_chat` 里，替换掉 `_chat` 等于把待测代码
    一起换走了（第一版就是这么写的，两个用例假红）。
    """

    def fake_post(url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(status, request=request, json={"error": {"message": "invalid key"}})

    monkeypatch.setattr(summarizer_module.httpx, "post", fake_post)


def test_summarize_byok_rejected_key_reports_byok_code(monkeypatch):
    """用户 Key 被 401 拒绝时必须报 BYOK_KEY_INVALID，而不是「站点未配置」。"""
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "site-key")
    _fail_http_with_status(monkeypatch, 401)

    response = client.post(
        "/api/summarize",
        json={"transcript": TRANSCRIPT, "title": "标题", "duration": 60},
        headers={HEADER_LLM_KEY: "bad-key"},
    )
    assert response.status_code == 401
    body = response.json()["error"]
    assert body["code"] == "BYOK_KEY_INVALID"
    # 提示要指向「API 设置」，让用户知道去哪改
    assert "API 设置" in body["message"]


def test_summarize_site_key_rejected_does_not_blame_user(monkeypatch):
    """站点自己的 Key 失效时不能说「你填的 Key 无效」——用户可能根本没填。"""
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "bad-site-key")
    _fail_http_with_status(monkeypatch, 401)

    response = client.post("/api/summarize", json={"transcript": TRANSCRIPT, "title": "标题", "duration": 60})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "SUMMARIZE_FAILED"


def test_summarize_response_never_echoes_key(monkeypatch):
    """响应体里绝不能出现 Key 本身（哪怕报错）。"""
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "site-key")
    captured: dict = {}
    _mock_chat_capture(monkeypatch, captured)

    secret = "sk-must-not-leak-1234567890"
    response = client.post(
        "/api/summarize",
        json={"transcript": TRANSCRIPT, "title": "标题", "duration": 60},
        headers={HEADER_LLM_KEY: secret},
    )
    assert secret not in response.text


# ---- 转写：请求级 Key 覆盖与预检 ----

def test_transcribe_cloud_without_any_key_reports_not_configured(monkeypatch):
    from app.routers import transcribe as transcribe_router

    monkeypatch.setattr(transcribe_router, "CLOUD_ASR_API_KEY", "")

    response = client.post("/api/transcribe", json={"videoId": VIDEO_ID, "engine": "cloud"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CLOUD_NOT_CONFIGURED"


def _capture_jobs(monkeypatch, transcribe_router) -> list:
    jobs: list = []

    def fake_enqueue(job, *args, **kwargs):
        jobs.append(job)

    monkeypatch.setattr(transcribe_router, "enqueue", fake_enqueue)
    return jobs


def test_transcribe_cloud_with_byok_key_passes_precheck(monkeypatch):
    """站点没配 ASR Key，但用户带了自己的 → 不该被 CLOUD_NOT_CONFIGURED 拦住。"""
    from app.routers import transcribe as transcribe_router

    monkeypatch.setattr(transcribe_router, "CLOUD_ASR_API_KEY", "")
    _capture_jobs(monkeypatch, transcribe_router)

    response = client.post(
        "/api/transcribe",
        json={"videoId": VIDEO_ID, "engine": "cloud"},
        headers={HEADER_ASR_KEY: "user-asr-key"},
    )
    assert response.status_code == 200
    assert response.json()["taskId"]


def test_transcribe_job_carries_byok_key(monkeypatch):
    from app.routers import transcribe as transcribe_router

    monkeypatch.setattr(transcribe_router, "CLOUD_ASR_API_KEY", "")
    jobs = _capture_jobs(monkeypatch, transcribe_router)

    client.post(
        "/api/transcribe",
        json={"videoId": VIDEO_ID, "engine": "cloud"},
        headers={HEADER_ASR_KEY: "user-asr-key"},
    )
    assert jobs and jobs[0].byok_key == "user-asr-key"


def test_transcribe_job_without_header_has_no_key(monkeypatch):
    from app.routers import transcribe as transcribe_router

    jobs = _capture_jobs(monkeypatch, transcribe_router)

    client.post("/api/transcribe", json={"videoId": VIDEO_ID, "engine": "local"})
    assert jobs and jobs[0].byok_key is None


# ---- ASR 引擎层：Key 选择与降级策略 ----

def _stub_httpx_post(monkeypatch, handler):
    """替换 asr 模块里的 httpx.post，其余 httpx 属性透传真实实现。"""
    monkeypatch.setattr(asr_module.httpx, "post", handler)


def test_cloud_engine_uses_byok_key_in_authorization_header(monkeypatch):
    monkeypatch.setattr(asr_module, "CLOUD_ASR_API_KEY", "site-asr-key")
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs.get("headers") or {})
        request = httpx.Request("POST", url)
        return httpx.Response(200, request=request, json={"text": "识别结果。"})

    _stub_httpx_post(monkeypatch, fake_post)

    asr_module.CloudASREngine().transcribe(io.BytesIO(b"fake"), byok_key="user-asr-key")
    assert captured["Authorization"] == "Bearer user-asr-key"


def test_cloud_engine_falls_back_to_site_key(monkeypatch):
    monkeypatch.setattr(asr_module, "CLOUD_ASR_API_KEY", "site-asr-key")
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs.get("headers") or {})
        request = httpx.Request("POST", url)
        return httpx.Response(200, request=request, json={"text": "识别结果。"})

    _stub_httpx_post(monkeypatch, fake_post)

    asr_module.CloudASREngine().transcribe(io.BytesIO(b"fake"))
    assert captured["Authorization"] == "Bearer site-asr-key"


def test_cloud_engine_byok_rejected_raises_byok_error(monkeypatch):
    monkeypatch.setattr(asr_module, "CLOUD_ASR_API_KEY", "site-asr-key")

    def fail_post(url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(401, request=request, json={"error": "invalid key"})

    _stub_httpx_post(monkeypatch, fail_post)

    with pytest.raises(Exception) as excinfo:
        asr_module.CloudASREngine().transcribe(io.BytesIO(b"fake"), byok_key="bad-key")
    assert getattr(excinfo.value, "code", None) == "BYOK_KEY_INVALID"


def test_byok_failure_does_not_fall_back_to_local(monkeypatch):
    """用户 Key 被拒时不降级到本地：降级会跑出结果，用户永远不知道自己的 Key 是坏的。"""
    monkeypatch.setattr(asr_module, "CLOUD_ASR_API_KEY", "site-asr-key")
    monkeypatch.setattr(asr_module, "ASR_ENGINE_FALLBACK", "local")

    def fail_post(url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(401, request=request, json={"error": "invalid key"})

    _stub_httpx_post(monkeypatch, fail_post)

    with pytest.raises(Exception) as excinfo:
        asr_module.transcribe_audio(io.BytesIO(b"fake"), engine="cloud", byok_key="bad-key")
    assert getattr(excinfo.value, "code", None) == "BYOK_KEY_INVALID"


def test_local_engine_ignores_byok_key(monkeypatch):
    """本地引擎收到 byok_key 不能报错（接口统一，参数要能被吞掉）。"""
    called: dict = {}

    def fake_transcribe(
        self, audio, language="zh", progress_callback=None, initial_prompt=None, hotwords=None, byok_key=None
    ):
        called["byok_key"] = byok_key
        return [{"time": 0.0, "text": "本地结果。"}]

    monkeypatch.setattr(asr_module.LocalWhisperEngine, "transcribe", fake_transcribe)
    items = asr_module.transcribe_audio(io.BytesIO(b"fake"), engine="local", byok_key="ignored")
    assert items[0]["text"] == "本地结果。"
    assert called["byok_key"] == "ignored"


# ---- 任务载荷 ----

def test_job_roundtrip_keeps_byok_key():
    from app.services.dispatcher import TranscribeJob

    job = TranscribeJob(task_id="t1", url=f"https://www.bilibili.com/video/{VIDEO_ID}", byok_key="user-key")
    restored = TranscribeJob.from_dict(job.to_dict())
    assert restored.byok_key == "user-key"


def test_job_from_legacy_dict_without_byok_key():
    from app.services.dispatcher import TranscribeJob

    job = TranscribeJob.from_dict({"task_id": "t1", "url": f"https://www.bilibili.com/video/{VIDEO_ID}"})
    assert job.byok_key is None


def test_task_store_payload_has_no_key_field():
    """Key 不落任务库：任务表的序列化结构里根本没有这个字段。"""
    from app.services.tasks import TranscribeTask, _payload_dict

    payload = _payload_dict(TranscribeTask(task_id="t1", status="queued"))
    assert not any("key" in name.lower() for name in payload)
