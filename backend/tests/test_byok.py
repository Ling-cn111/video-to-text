"""用户自带 Key（BYOK）测试：请求头解析、Key 覆盖、鉴权失败分流、不泄露 Key。"""
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app.services.asr as asr_module
import app.services.byok as byok_module
import app.services.summarizer as summarizer_module
import app.url_guard as url_guard_module
from app.main import app
from app.services.byok import (
    HEADER_ASR_KEY,
    HEADER_LLM_KEY,
    MAX_BASE_URL_LENGTH,
    MAX_KEY_LENGTH,
    MAX_MODEL_LENGTH,
    is_auth_failure,
    key_source,
    normalize_base_url,
    normalize_key,
    normalize_model,
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
        captured["base_url"] = base_url
        captured["model"] = model
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


# ---- 自定义服务商：接口地址与模型名 ----
#
# 这组用例守的是「别家的 Key 用不了」这个反馈的根因：旧实现只让用户填 Key，
# base_url 与 model 钉死在 LLM_PRESETS，用户填 OpenAI / Moonshot 的 Key 后
# 后端仍去请求 DeepSeek，必然 401。修法是让地址与模型也能由用户指定，
# 同时地址必须过 SSRF 校验（否则等于给浏览器开了一个内网探测接口）。


def test_normalize_base_url_keeps_public_address():
    assert normalize_base_url("https://api.openai.com/v1") == "https://api.openai.com/v1"
    # 末尾斜杠去掉：调用方统一 f"{base_url}/chat/completions"，双斜杠有的网关会 404
    assert normalize_base_url("https://api.moonshot.cn/v1/") == "https://api.moonshot.cn/v1"
    # 不做「必须以 /v1 结尾」的假设：有的服务商是 /openai/v1，有的直接用根路径
    assert normalize_base_url("https://open.bigmodel.cn/api/paas/v4") == "https://open.bigmodel.cn/api/paas/v4"


def test_normalize_base_url_treats_blank_as_absent():
    """空串 / 全空白 / None 都视为「没填」，回落站点配置，而不是报错。"""
    assert normalize_base_url(None) is None
    assert normalize_base_url("") is None
    assert normalize_base_url("   ") is None


@pytest.mark.parametrize(
    "raw",
    [
        "http://127.0.0.1:8080/v1",
        "http://localhost:11434/v1",
        "http://169.254.169.254/latest/meta-data",  # 云厂商 metadata：SSRF 的经典目标
        "http://10.0.0.5/v1",
        "http://192.168.1.1/v1",
        "http://[::1]:8000/v1",
        "http://router.local/v1",
        "file:///etc/passwd",
        "ftp://example.com/v1",
    ],
)
def test_normalize_base_url_rejects_private_and_non_http(raw):
    """环回 / 私有 / 链路本地 / 非 http(s) 一律拒绝：这是 SSRF 的唯一闸门。"""
    from app.errors import ByokEndpointInvalidError

    with pytest.raises(ByokEndpointInvalidError):
        normalize_base_url(raw)


def test_normalize_base_url_rejects_overlong():
    from app.errors import ByokEndpointInvalidError

    with pytest.raises(ByokEndpointInvalidError):
        normalize_base_url("https://example.com/" + "a" * MAX_BASE_URL_LENGTH)


def test_normalize_model_trims_and_rejects_spaces():
    from app.errors import ByokEndpointInvalidError

    assert normalize_model("  deepseek-chat  ") == "deepseek-chat"
    assert normalize_model(None) is None
    assert normalize_model("") is None
    # 含空格多半是从别处连名字带描述一起复制了，发过去只会得到「model not found」
    with pytest.raises(ByokEndpointInvalidError):
        normalize_model("deepseek chat")
    with pytest.raises(ByokEndpointInvalidError):
        normalize_model("m" * (MAX_MODEL_LENGTH + 1))


def test_summarize_uses_user_base_url_and_model(monkeypatch):
    """用户填了地址与模型 → 实际发出去的必须是他的，而不是站点预设。"""
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "site-key")
    captured: dict = {}
    _mock_chat_capture(monkeypatch, captured)

    response = client.post(
        "/api/summarize",
        json={
            "transcript": TRANSCRIPT,
            "title": "标题",
            "duration": 60,
            "llmBaseUrl": "https://api.moonshot.cn/v1",
            "llmModel": "moonshot-v1-8k",
        },
        headers={HEADER_LLM_KEY: "user-key"},
    )
    assert response.status_code == 200
    assert captured["base_url"] == "https://api.moonshot.cn/v1"
    assert captured["model"] == "moonshot-v1-8k"
    assert captured["api_key"] == "user-key"


def test_summarize_without_user_base_url_uses_preset(monkeypatch):
    """没填地址与模型时行为与旧版完全一致（回落 LLM_PRESETS）。"""
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "site-key")
    monkeypatch.setattr(summarizer_module, "LLM_MODEL", "")
    captured: dict = {}
    _mock_chat_capture(monkeypatch, captured)

    response = client.post("/api/summarize", json={"transcript": TRANSCRIPT, "title": "标题", "duration": 60})
    assert response.status_code == 200
    preset = summarizer_module.LLM_PRESETS[summarizer_module.LLM_PROVIDER]
    assert captured["base_url"] == preset["base_url"]
    assert captured["model"] == preset["default_model"]


def test_summarize_rejects_custom_base_url_without_model(monkeypatch):
    """只填地址不填模型 → 提前拒绝。

    站点默认模型（如 deepseek-flash）在别家服务商那里不存在，发过去只会得到
    一句「model not found」，用户还得自己猜是哪里错了。与其让上游报错，
    不如在入口说清楚「还差一个模型名」。
    """
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "site-key")
    _mock_chat_capture(monkeypatch, {})

    response = client.post(
        "/api/summarize",
        json={
            "transcript": TRANSCRIPT,
            "title": "标题",
            "duration": 60,
            "llmBaseUrl": "https://api.openai.com/v1",
        },
        headers={HEADER_LLM_KEY: "user-key"},
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "BYOK_ENDPOINT_INVALID"
    assert "模型名" in error["message"]


def test_summarize_rejects_ssrf_base_url_at_http_layer(monkeypatch):
    """内网地址必须被 HTTP 层挡住，且错误码指向「API 设置」而不是「链接无效」。"""
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "site-key")
    _mock_chat_capture(monkeypatch, {})

    response = client.post(
        "/api/summarize",
        json={
            "transcript": TRANSCRIPT,
            "title": "标题",
            "duration": 60,
            "llmBaseUrl": "http://169.254.169.254/latest/meta-data",
            "llmModel": "x",
        },
        headers={HEADER_LLM_KEY: "user-key"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BYOK_ENDPOINT_INVALID"


def test_summarize_user_base_url_does_not_leak_into_logs(monkeypatch, caplog):
    """日志里可以出现「用了自定义地址」这个事实，但不得出现 Key。"""
    import logging

    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "site-key")
    _mock_chat_capture(monkeypatch, {})

    with caplog.at_level(logging.INFO):
        client.post(
            "/api/summarize",
            json={
                "transcript": TRANSCRIPT,
                "title": "标题",
                "duration": 60,
                "llmBaseUrl": "https://api.openai.com/v1",
                "llmModel": "gpt-4o-mini",
            },
            headers={HEADER_LLM_KEY: "sk-super-secret-user-key"},
        )
    assert "sk-super-secret-user-key" not in caplog.text
    assert "gpt-4o-mini" in caplog.text


# ---------------------------------------------------------------------------
# 安全加固：SSRF 绕过与站点 Key 外泄
# ---------------------------------------------------------------------------


def test_normalize_base_url_rejects_hostname_resolving_to_loopback(monkeypatch):
    """域名解析到环回地址也必须被拒（`is_blocked_host` 只看字面量，挡不住）。

    `127.0.0.1.nip.io` / `localtest.me` 这类公共 DNS 服务会把任意 IP 编进域名，
    攻击者不需要自己的域名就能构造出「看起来是公网域名、实际指向 127.0.0.1」的
    地址。只查字面量等于没设防。
    """
    import socket

    def fake_getaddrinfo(host, *args, **kwargs):
        assert host == "evil.example.com"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(url_guard_module.socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(byok_module.ByokEndpointInvalidError) as excinfo:
        byok_module.normalize_base_url("https://evil.example.com/v1")
    assert "内网" in str(excinfo.value)


def test_normalize_base_url_rejects_hostname_resolving_to_private_ip(monkeypatch):
    """解析到 10/172/192.168 等私网段同样拒绝。"""
    import socket

    monkeypatch.setattr(
        url_guard_module.socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))],
    )

    with pytest.raises(byok_module.ByokEndpointInvalidError):
        byok_module.normalize_base_url("https://looks-public.example.com/v1")


def test_normalize_base_url_rejects_when_dns_lookup_fails(monkeypatch):
    """解析失败按不安全处理：连不上和连到内网，宁可直接拒。"""

    def boom(*args, **kwargs):
        raise OSError("dns down")

    monkeypatch.setattr(url_guard_module.socket, "getaddrinfo", boom)

    with pytest.raises(byok_module.ByokEndpointInvalidError):
        byok_module.normalize_base_url("https://unresolvable.example.com/v1")


def test_normalize_base_url_accepts_public_hostname(monkeypatch):
    """正常公网域名必须放行，否则功能等于不可用。"""
    import socket

    monkeypatch.setattr(
        url_guard_module.socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.0.1", 0))],
    )

    assert byok_module.normalize_base_url("https://api.openai.com/v1") == "https://api.openai.com/v1"


def test_shared_address_space_is_blocked():
    """RFC 6598 共享地址段（100.64.0.0/10）必须拦。

    `ipaddress` 不把这段算进 is_private，但阿里云 metadata 就在 100.100.100.200，
    不单独处理就是一个现成的内网入口。
    """
    from app.url_guard import is_blocked_host

    assert is_blocked_host("100.100.100.200") is True
    assert is_blocked_host("100.64.0.1") is True
    assert is_blocked_host("100.63.255.255") is False  # 段外边界，不该误伤


def test_summarize_rejects_custom_base_url_without_key(monkeypatch):
    """只填地址不填 Key → 必须拒绝，否则站点 Key 会被发往用户指定的地址。

    这是本次最严重的一处：`_resolve_provider` 的 Key 回落目标是站点自己的 Key。
    若不拦，任何人手写一个不带 `X-Vtt-Llm-Key` 的请求、只指定自己的地址，后端就会
    拿站点 Key 去敲那台服务器。`/api/summarize` 无鉴权，等于公开泄露。
    """
    import socket

    # 让这个地址「解析到公网」，否则会先被 DNS 复检拦下，测不到 Key 这条规则
    monkeypatch.setattr(
        url_guard_module.socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.0.1", 0))],
    )
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "sk-site-secret")
    _mock_chat_capture(monkeypatch, {})

    response = client.post(
        "/api/summarize",
        json={
            "transcript": TRANSCRIPT,
            "title": "标题",
            "duration": 60,
            "llmBaseUrl": "https://attacker.example.com/v1",
            "llmModel": "gpt-4o-mini",
        },
        # 故意不带 Key 请求头
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "BYOK_ENDPOINT_INVALID"
    assert "Key" in error["message"]


def test_resolve_provider_never_pairs_site_key_with_user_base_url(monkeypatch):
    """直接测 `_resolve_provider`：任何「用户地址 + 站点 Key」的组合都不允许出现。"""
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "sk-site-secret")

    with pytest.raises(summarizer_module.ByokEndpointInvalidError):
        summarizer_module._resolve_provider(
            None, "https://attacker.example.com/v1", "gpt-4o-mini"
        )


def test_resolve_provider_allows_user_key_without_base_url(monkeypatch):
    """最常见的用法必须保留：只换 Key、地址与模型用站点的。"""
    monkeypatch.setattr(summarizer_module, "DEEPSEEK_API_KEY", "sk-site-secret")
    base_url, api_key, model = summarizer_module._resolve_provider("sk-user-key", None, None)

    assert api_key == "sk-user-key"
    assert base_url == summarizer_module.LLM_PRESETS[summarizer_module.LLM_PROVIDER]["base_url"]
    assert model == (
        summarizer_module.LLM_MODEL
        or summarizer_module.LLM_PRESETS[summarizer_module.LLM_PROVIDER]["default_model"]
    )
