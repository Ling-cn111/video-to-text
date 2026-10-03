"""解析接口测试。

标记说明：
- 无标记：纯单元测试（不访问外网）
- @pytest.mark.network：真实调用 B站 / yt-dlp 的集成测试，CI 中默认跳过，
  本地验证时用 `pytest` 全量运行（或 `pytest -m network` 只跑集成）。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# 稳定的知名视频（Rick Astley 官方转载 MV），长期可用
REAL_BILIBILI_URL = "https://www.bilibili.com/video/BV1GJ411x7h7"


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_parse_rejects_invalid_url():
    response = client.post("/api/parse", json={"url": "not-a-url"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_URL"


def test_parse_rejects_unsupported_platform():
    response = client.post(
        "/api/parse", json={"url": "https://v.qq.com/x/cover/mzc00200xxxx.html"}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "UNSUPPORTED_PLATFORM"
    assert "v.qq.com" in body["error"]["message"]


def test_parse_rejects_private_and_loopback_hosts():
    """SSRF 约束：环回 / 私有网段一律拒绝，即使伪造平台路径。"""
    for url in (
        "http://localhost/video/BV1GJ411x7h7",
        "http://127.0.0.1/video/BV1GJ411x7h7",
        "http://192.168.1.10/video/BV1GJ411x7h7",
        "http://[::1]/video/BV1GJ411x7h7",
        "http://10.0.0.5/video/BV1GJ411x7h7",
    ):
        response = client.post("/api/parse", json={"url": url})
        assert response.status_code == 400, url
        assert response.json()["error"]["code"] == "INVALID_URL", url


def test_parse_rejects_missing_url():
    response = client.post("/api/parse", json={})
    assert response.status_code == 422  # pydantic 校验失败


@pytest.mark.network
def test_parse_real_bilibili_video():
    """真实 B站链接解析：返回结构与前端 types.ts 的 VideoInfo 完全一致。"""
    response = client.post("/api/parse", json={"url": REAL_BILIBILI_URL})
    assert response.status_code == 200
    data = response.json()

    # 契约字段：title / cover / duration / platform / videoId，不多不少
    assert set(data.keys()) == {"title", "cover", "duration", "platform", "videoId"}
    assert data["platform"] == "bilibili"
    assert data["videoId"] == "BV1GJ411x7h7"
    assert isinstance(data["title"], str) and len(data["title"]) > 0
    assert data["cover"].startswith("http")
    assert isinstance(data["duration"], int) and data["duration"] > 0


@pytest.mark.network
def test_parse_cors_allows_local_frontend():
    """CORS：本地前端域名可跨域访问（预检与实际响应都带allow-origin头）。"""
    headers = {"Origin": "http://localhost:3000"}
    preflight = client.options(
        "/api/parse",
        headers={**headers, "Access-Control-Request-Method": "POST"},
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"

    response = client.post("/api/parse", json={"url": REAL_BILIBILI_URL}, headers=headers)
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
