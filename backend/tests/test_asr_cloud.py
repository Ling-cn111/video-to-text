"""云端 ASR 引擎手动切换测试（全部 mock，不访问外网）。

覆盖：
- local 别名映射 faster-whisper（small 默认）与引擎实例按名缓存
- CloudASREngine 请求形状 / verbose_json 解析 / 未配 Key 友好错误 / 私网服务地址拒绝
- 云端失败按 ASR_ENGINE_FALLBACK 降级本地（降级禁用时抛原始错误）
- 提交接口 engine=cloud 且未配 Key → 400 CLOUD_NOT_CONFIGURED（不静默降级）
"""
import httpx
import pytest
from fastapi.testclient import TestClient

import app.services.asr as asr_module
from app.config import WHISPER_MODEL
from app.main import app
from app.services import tasks as tasks_module
from app.services.asr import CloudASREngine, LocalWhisperEngine, build_engine, get_engine, transcribe_audio

client = TestClient(app)

VIDEO_BODY = {
    "videoId": "BV1GJ411x7h7",
    "video": {
        "title": "测试视频",
        "cover": "https://i1.hdslb.com/bfs/archive/test.jpg",
        "duration": 135,
        "platform": "bilibili",
        "videoId": "BV1GJ411x7h7",
    },
}


class _FakeCloudResponse:
    """模拟 OpenAI 兼容 /audio/transcriptions 响应（含 400 降级场景）。"""

    def __init__(self, payload: dict | None = None, status_code: int = 200):
        self._payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://cloud.test/v1/audio/transcriptions")
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=request,
                response=httpx.Response(self.status_code, request=request),
            )

    def json(self) -> dict:
        return self._payload


# ---- 引擎选择与缓存 ----

def test_local_alias_maps_to_faster_whisper_small():
    engine = build_engine("local")
    assert isinstance(engine, LocalWhisperEngine)
    assert engine.name == "local"
    # ASR_ENGINE=local 走 faster-whisper，模型规格与全局配置一致（默认 small）
    assert engine._model_size == WHISPER_MODEL


def test_engine_instances_cached_by_name():
    assert get_engine("cloud") is get_engine("cloud")
    assert get_engine("local") is get_engine("local")
    assert isinstance(get_engine("cloud"), CloudASREngine)


def test_unknown_engine_raises():
    with pytest.raises(Exception, match="未知 ASR 引擎"):
        build_engine("nope")


# ---- CloudASREngine（mock httpx）----

def test_cloud_request_shape_and_verbose_json_parsing(monkeypatch, tmp_path):
    monkeypatch.setattr(asr_module, "CLOUD_ASR_API_KEY", "test-key")
    monkeypatch.setattr(asr_module, "CLOUD_ASR_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setattr(asr_module, "CLOUD_ASR_MODEL", "XingChenAGI/XingChenASR-V3.2-Ultra")

    captured: dict = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return _FakeCloudResponse(
            {
                "segments": [
                    {"start": 0.5, "text": "第一句内容"},
                    {"start": 2.1, "text": "second sentence"},
                ]
            }
        )

    monkeypatch.setattr(asr_module.httpx, "post", fake_post)

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")

    items = CloudASREngine().transcribe(audio, language="zh", initial_prompt="标题")

    assert captured["url"] == "https://api.siliconflow.cn/v1/audio/transcriptions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["data"]["model"] == "XingChenAGI/XingChenASR-V3.2-Ultra"
    assert captured["data"]["response_format"] == "verbose_json"
    assert captured["data"]["language"] == "zh"
    assert captured["data"]["prompt"] == "标题"
    assert captured["files"]["file"][0] == "audio.wav"
    # 分段时间戳保留、后处理补句末标点
    assert items[0] == {"time": 0.5, "text": "第一句内容。"}
    assert items[1]["time"] == 2.1


def test_cloud_falls_back_to_json_when_verbose_json_unsupported(monkeypatch, tmp_path):
    """服务商不支持 verbose_json（400）时自动降级 json：整段单条输出。"""
    monkeypatch.setattr(asr_module, "CLOUD_ASR_API_KEY", "test-key")
    monkeypatch.setattr(asr_module, "CLOUD_ASR_BASE_URL", "https://api.siliconflow.cn/v1")

    formats_seen: list[str] = []

    def fake_post(url, data=None, **kwargs):
        formats_seen.append(data["response_format"])
        if data["response_format"] == "verbose_json":
            return _FakeCloudResponse(status_code=400)
        return _FakeCloudResponse({"text": "整段全文内容"})

    monkeypatch.setattr(asr_module.httpx, "post", fake_post)

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")
    items = CloudASREngine().transcribe(audio)

    assert formats_seen == ["verbose_json", "json"]  # 先试分段，400 后降级
    assert items == [{"time": 0.0, "text": "整段全文内容。"}]


def test_cloud_persists_400_after_fallback_raises(monkeypatch, tmp_path):
    """降级 json 后仍 400（如参数/鉴权问题）：抛友好错误而非静默返回空。"""
    monkeypatch.setattr(asr_module, "CLOUD_ASR_API_KEY", "test-key")
    monkeypatch.setattr(asr_module, "CLOUD_ASR_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setattr(asr_module.httpx, "post", lambda url, **kwargs: _FakeCloudResponse(status_code=400))

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")
    with pytest.raises(Exception, match="HTTP 400"):
        CloudASREngine().transcribe(audio)


def test_cloud_missing_key_friendly_chinese_error(monkeypatch, tmp_path):
    monkeypatch.setattr(asr_module, "CLOUD_ASR_API_KEY", "")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")

    with pytest.raises(Exception) as exc_info:
        CloudASREngine().transcribe(audio)
    assert "CLOUD_ASR_API_KEY" in str(exc_info.value)
    assert "云端" in str(exc_info.value)


@pytest.mark.parametrize("blocked_base", ["http://127.0.0.1:8000/v1", "http://192.168.1.10/v1", "ftp://api.example.com/v1"])
def test_cloud_rejects_private_or_non_http_base_url(monkeypatch, tmp_path, blocked_base):
    monkeypatch.setattr(asr_module, "CLOUD_ASR_API_KEY", "test-key")
    monkeypatch.setattr(asr_module, "CLOUD_ASR_BASE_URL", blocked_base)
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")

    with pytest.raises(Exception, match="CLOUD_ASR_BASE_URL"):
        CloudASREngine().transcribe(audio)


# ---- 失败降级（ASR_ENGINE_FALLBACK）----

def test_cloud_failure_falls_back_to_local(monkeypatch, tmp_path):
    monkeypatch.setattr(asr_module, "CLOUD_ASR_API_KEY", "test-key")

    def fail_post(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(asr_module.httpx, "post", fail_post)
    monkeypatch.setattr(
        asr_module.LocalWhisperEngine, "transcribe",
        lambda self, audio_path, language="zh", progress_callback=None, initial_prompt=None, hotwords=None: [
            {"time": 0.0, "text": "本地降级结果"}
        ],
    )

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")
    items = transcribe_audio(audio, engine="cloud")
    assert items and "本地降级结果" in items[0]["text"]


def test_cloud_failure_without_fallback_raises_original(monkeypatch, tmp_path):
    monkeypatch.setattr(asr_module, "CLOUD_ASR_API_KEY", "test-key")
    monkeypatch.setattr(asr_module, "ASR_ENGINE_FALLBACK", "")  # 显式禁用降级

    def fail_post(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(asr_module.httpx, "post", fail_post)

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")
    with pytest.raises(Exception, match="连接失败"):
        transcribe_audio(audio, engine="cloud")


# ---- 提交接口：云端未配 Key 提交即报错，不创建任务 ----

@pytest.fixture(autouse=True)
def _noop_pipeline(monkeypatch):
    """拦截后台流水线：接口层测试不触发真实下载/转写。"""
    monkeypatch.setattr("app.routers.transcribe.run_transcription_pipeline", lambda *args, **kwargs: None)


def test_submit_cloud_without_key_rejected_before_task_creation(monkeypatch):
    monkeypatch.setattr("app.routers.transcribe.CLOUD_ASR_API_KEY", "")
    tasks_before = len(tasks_module._TASKS)

    response = client.post("/api/transcribe", json={**VIDEO_BODY, "engine": "cloud"})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "CLOUD_NOT_CONFIGURED"
    assert "CLOUD_ASR_API_KEY" in body["error"]["message"]
    assert len(tasks_module._TASKS) == tasks_before  # 未静默创建任务


def test_submit_local_creates_task_with_engine(monkeypatch):
    monkeypatch.setattr("app.routers.transcribe.CLOUD_ASR_API_KEY", "")

    response = client.post("/api/transcribe", json={**VIDEO_BODY, "engine": "local"})
    assert response.status_code == 200
    task_id = response.json()["taskId"]
    assert tasks_module.get_task(task_id).engine == "local"


def test_submit_cloud_with_key_creates_task(monkeypatch):
    monkeypatch.setattr("app.routers.transcribe.CLOUD_ASR_API_KEY", "test-key")

    response = client.post("/api/transcribe", json={**VIDEO_BODY, "engine": "cloud"})
    assert response.status_code == 200
    task_id = response.json()["taskId"]
    assert tasks_module.get_task(task_id).engine == "cloud"


def test_submit_without_engine_defaults_to_env_engine(monkeypatch):
    # 缺省不传 engine：跟随后端 ASR_ENGINE（测试进程默认 faster-whisper，非 cloud，不触发预检）
    response = client.post("/api/transcribe", json=VIDEO_BODY)
    assert response.status_code == 200
    task_id = response.json()["taskId"]
    assert tasks_module.get_task(task_id).engine == asr_module.ASR_ENGINE
