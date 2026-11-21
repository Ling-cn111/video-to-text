"""上传路由（M3）：签发预签名 PUT 凭证，后端**不接收任何文件字节**。

为什么这个端点只有 JSON：FC HTTP 触发器同步请求体上限 32MB（平台层限制），
文件字节从浏览器直传 OSS；本接口只做三件事——

1. 校验声明（扩展名白名单 + 大小上限）——第一重大小校验
2. 生成 objectKey（uploads/{uuid}/{净化文件名}），预签名 URL 只对它有效
3. 签发限时 PUT URL 返回给前端

防御要点：
- objectKey 由后端生成，客户端拿不到「写任意位置」的能力；后续 transcribe
  与 Worker 侧还会按格式复核（services/storage.py 的 _require_valid_key）
- 扩展名取自原始文件名并小写化；mime 由浏览器声明，**不参与白名单判断**
  （浏览器可能给 video/mp4、application/octet-stream 甚至空串，只作签名用）
"""
import logging
import re
import uuid
from pathlib import PurePosixPath

from fastapi import APIRouter

from app.config import FILE_ALLOWED_EXTENSIONS, FILE_MAX_SIZE_MB, OSS_UPLOAD_PREFIX
from app.errors import FileFormatUnsupportedError, FileTooLargeError
from app.schemas import UploadInitRequest, UploadInitResponse
from app.services import storage

logger = logging.getLogger(__name__)

router = APIRouter()

# 文件名净化：除字母数字、点、下划线、连字符外全部替换为「_」，并限长。
# 只影响 objectKey（用户可见标题用原始 fileName），不改变扩展名语义。
_UNSAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]")


def _file_max_bytes() -> int:
    return FILE_MAX_SIZE_MB * 1024 * 1024


def sanitize_object_name(file_name: str) -> str:
    """从原始文件名生成安全的对象名：保扩展名、杀路径、限长度。"""
    base = PurePosixPath(file_name.replace("\\", "/")).name  # 只取 basename，杀路径
    sanitized = _UNSAFE_CHARS_RE.sub("_", base)
    stem, _, ext = sanitized.rpartition(".")
    if not ext or len(ext) > 8:
        # 没有可辨识扩展名：文件名整体作为 stem（调用方已先做扩展名校验，罕见）
        return sanitized[:120] or "video.bin"
    return f"{stem[:110]}_.{ext}" if stem else f"_{ext}"


def allowed_extensions() -> set[str]:
    return {ext.strip().lower() for ext in FILE_ALLOWED_EXTENSIONS.split(",") if ext.strip()}


@router.post("/api/upload/init", response_model=UploadInitResponse)
def init_upload(payload: UploadInitRequest) -> UploadInitResponse:
    # 未配置 OSS：明确报错（400 FILE_STORAGE_NOT_CONFIGURED），
    # 正常流程中前端会先看 capabilities.fileUploadConfigured 隐藏入口
    storage.require_file_storage()

    file_name = (payload.fileName or "").strip()
    ext = PurePosixPath(file_name.replace("\\", "/")).suffix.lower().lstrip(".")
    if ext not in allowed_extensions():
        supported = " / ".join(sorted(allowed_extensions()))
        raise FileFormatUnsupportedError(f"暂不支持「{ext or '无扩展名'}」格式，请上传 {supported} 视频")

    size = payload.size
    if size is None or size <= 0:
        raise FileTooLargeError("文件大小异常，请重新选择文件")
    if size > _file_max_bytes():
        raise FileTooLargeError(f"文件超过 {FILE_MAX_SIZE_MB}MB 上限，请压缩或裁剪后再上传")

    object_key = f"{OSS_UPLOAD_PREFIX}{uuid.uuid4().hex}/{sanitize_object_name(file_name)}"
    upload_url = storage.presign_put(object_key, (payload.mimeType or "").strip())
    from app.config import OSS_PRESIGN_EXPIRES_SECONDS

    return UploadInitResponse(uploadUrl=upload_url, objectKey=object_key, expiresIn=OSS_PRESIGN_EXPIRES_SECONDS)