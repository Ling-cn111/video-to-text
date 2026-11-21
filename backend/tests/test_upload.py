"""本地上传路由测试（M3）：POST /api/upload/init 的签发与拒绝。

覆盖三重大小校验的第一重（声明校验）、扩展名白名单、objectKey 生成与
未配置 OSS 的明确报错。签名本身是 oss2 的离线 HMAC，真值由阿里云校验，
这里只断言「把正确的 key/方法/时效交给了签名函数」。
"""
import re

import pytest
from fastapi.testclient import TestClient

from app.config import FILE_MAX_SIZE_MB, OSS_PRESIGN_EXPIRES_SECONDS, OSS_UPLOAD_PREFIX
from app.main import app
from app.routers.upload import sanitize_object_name
from app.services import storage

client = TestClient(app)

_VALID_BODY = {
    "fileName": "课堂录像.mp4",
    "size": 123 * 1024 * 1024,
    "mimeType": "video/mp4",
}


@pytest.fixture(autouse=True)
def _oss_configured(monkeypatch):
    """测试环境不配 OSS：把「已配置」固定为真，签名调用换成可检查的桩。"""
    monkeypatch.setattr(storage, "is_file_storage_configured", lambda: True)
    signed = {}

    def _fake_presign(object_key: str, content_type: str) -> str:
        signed["key"] = object_key
        signed["content_type"] = content_type
        return f"https://oss.example.com/{object_key}?Signature=stub"

    monkeypatch.setattr(storage, "presign_put", _fake_presign)
    return signed


def test_init_issues_presigned_put_with_generated_key(_oss_configured):
    response = client.post("/api/upload/init", json=_VALID_BODY)
    assert response.status_code == 200
    body = response.json()

    assert body["uploadUrl"].startswith("https://oss.example.com/uploads/")
    assert body["expiresIn"] == OSS_PRESIGN_EXPIRES_SECONDS
    # objectKey 形态：uploads/{32位hex}/{净化文件名}——中文文件名净化为安全字符
    key = body["objectKey"]
    assert key.startswith(OSS_UPLOAD_PREFIX)
    assert key.endswith(".mp4")
    assert storage.checked_key(key)  # 白名单复核通过
    assert _oss_configured["key"] == key  # 签名桩拿到的正是生成的 key
    assert _oss_configured["content_type"] == "video/mp4"


def test_init_rejects_unsupported_extension(_oss_configured):
    response = client.post("/api/upload/init", json={**_VALID_BODY, "fileName": "demo.avi"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FILE_FORMAT_UNSUPPORTED"


def test_init_rejects_oversize_declaration(_oss_configured):
    over = (FILE_MAX_SIZE_MB + 1) * 1024 * 1024
    response = client.post("/api/upload/init", json={**_VALID_BODY, "size": over})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_init_rejects_non_positive_size(_oss_configured):
    for bad in (0, -1):
        response = client.post("/api/upload/init", json={**_VALID_BODY, "size": bad})
        assert response.status_code == 413


def test_init_without_oss_configured_gives_explicit_error(monkeypatch):
    """未配置 OSS：400 + FILE_STORAGE_NOT_CONFIGURED，而不是 500 或静默伪造签名。"""
    monkeypatch.setattr(storage, "is_file_storage_configured", lambda: False)
    response = client.post("/api/upload/init", json=_VALID_BODY)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "FILE_STORAGE_NOT_CONFIGURED"


def test_sanitize_object_name_kills_path_and_unsafe_chars():
    """文件名净化：路径分量、非法字符都进不了 objectKey（Mimosa 要求的安全写法）。"""
    assert sanitize_object_name("..\\..\\etc\\passwd.mp4").endswith(".mp4")
    for name in (
        "../../etc/passwd.mp4",
        "C:\\Users\\x\\a b(1).mp4",
        "我的 视频/片段.mp4",
        "a" * 300 + ".mp4",
    ):
        key = sanitize_object_name(name)
        assert "/" not in key and "\\" not in key
        assert re.fullmatch(r"[A-Za-z0-9._-]+", key), key
        assert len(key) <= 120
