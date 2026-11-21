"""OSS 对象存储薄封装（M3 本地文件直接上传）：后端只碰「引用」，不碰文件字节。

角色分工（为什么后端不收文件流）：

- API 函数（本模块的 presign_put / head_size）：**离线签名 + 元信息复核**，
  签发预签名 PUT URL 不产生任何网络请求（签名是本地 HMAC 计算），HEAD 只拉
  content-length 大小一个数字——FC 的 32MB 请求体限制与 API 函数的 2GB 内存都碰不到。
- Worker（download_to_temp / delete_object）：经 **内网 endpoint** 下载对象到
  /tmp（避免 500MB 源文件进内存——audio.py 的「音频全程内存化」只针对转码后
  ≤100MB 的 WAV）。转写终态后删对象（成功删 / 失败留 ≤1 天给重试，bucket
  生命周期规则兜底，见 docs/serverless-deploy.md）。

oss2 全部延迟导入：未配置 OSS 的部署（本地开发、Mock/E2E）import 本模块零开销，
`is_file_storage_configured()` 的探测语义与 asr.local_engine_available 同型——
探测绝不抛异常，前端靠 /api/capabilities 的 fileUploadConfigured 提前隐藏入口。
"""
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from typing import Iterator

from app.config import (
    OSS_ACCESS_KEY_ID,
    OSS_ACCESS_KEY_SECRET,
    OSS_BUCKET,
    OSS_ENDPOINT,
    OSS_INTERNAL_ENDPOINT,
    OSS_UPLOAD_PREFIX,
)
from app.errors import (
    FileStorageNotConfiguredError,
    FileTooLargeError,
    ParseFailedError,
)

logger = logging.getLogger(__name__)

# 合法 objectKey 形态：uploads/{32位hex}/{净化文件名}。upload/init 生成、
# transcribe-init 与 Worker 双重复核——伪造的派发载荷（需要 WORKER_TOKEN）也带不进来。
_OBJECT_KEY_RE = re.compile(r"^uploads/[0-9a-f]{32}/[A-Za-z0-9._-]+$")


def is_file_storage_configured() -> bool:
    """OSS 四项配置是否齐备（探测语义：绝不抛异常）。"""
    if not (OSS_BUCKET and OSS_ENDPOINT and OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET):
        return False
    try:
        import oss2  # noqa: F401
    except Exception:
        return False
    return True


def require_file_storage() -> None:
    """未配置时抛 FILE_STORAGE_NOT_CONFIGURED（400）：调用方是 /api/upload/init。"""
    if not is_file_storage_configured():
        raise FileStorageNotConfiguredError()


def _require_valid_key(object_key: str) -> str:
    """规范化并校验对象键，返回规范化后的键；不合法即抛错。

    OSS 对象键按路径语义审计：统一分隔符 → 显式拒绝「..」段 → 白名单正则。
    三步都过才允许出现在任何 OSS API 调用里；upload/init 生成的键天然满足，
    伪造载荷在此被拦下。
    """
    key = (object_key or "").replace("\\", "/")
    segments = [seg for seg in key.split("/") if seg]
    if not key or any(seg == ".." for seg in segments):
        raise ParseFailedError("文件引用不合法")
    if not _OBJECT_KEY_RE.match(key):
        raise ParseFailedError("文件引用不合法")
    return key


def checked_key(object_key: str) -> str:
    """公开入口：校验并规范化对象键（router / worker 用），不合法抛 ParseFailedError。"""
    return _require_valid_key(object_key)


def _bucket(endpoint: str | None = None):
    import oss2

    return oss2.Bucket(
        oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET),
        endpoint or OSS_ENDPOINT,
        OSS_BUCKET,
    )


def presign_put(object_key: str, content_type: str) -> str:
    """签发预签名 PUT URL（离线签名，无网络请求）。

    URL 只对这一个 key 有效、只允许 PUT、限时（OSS_PRESIGN_EXPIRES_SECONDS）。
    content_type 参与签名：客户端 PUT 时必须带完全一致的 Content-Type 头，
    否则 OSS 返回 SignatureDoesNotMatch——前端 XHR 会显式设置同一值。
    """
    import oss2  # noqa: F401  配置探测已过但包真缺失时，这里给明确报错而非 AttributeError

    key = _require_valid_key(object_key)
    headers = {"Content-Type": content_type} if content_type else None
    from app.config import OSS_PRESIGN_EXPIRES_SECONDS

    return _bucket().sign_url("PUT", key, OSS_PRESIGN_EXPIRES_SECONDS, headers=headers)


def head_size(object_key: str) -> int | None:
    """HEAD 对象返回字节数；不存在或键不合法返回 None（调用方转 FILE_NOT_FOUND）。"""
    import oss2.exceptions

    try:
        key = _require_valid_key(object_key)
        return int(_bucket().head_object(key).content_length)
    except oss2.exceptions.NotFound:
        return None


@contextmanager
def download_to_temp(object_key: str, max_bytes: int) -> Iterator[str]:
    """把 OSS 对象下载到系统临时目录，yield 临时文件路径，退出即删。

    为什么落盘而不是进内存：源文件上限 500MB，Worker 只有 4GB 内存，整块读入
    必然 OOM。下载走 oss2 官方的 get_object_to_file（内部流式落盘，不整块进
    内存）；大小校验改为「下载后 getsize 复核」——HEAD 层（建任务时）已经校验
    过实际大小，这层是下载窗口期对象被重写的 TOCTOU 兜底。

    yield 期间调用方用该路径喂 ffmpeg（convert_file_to_wav_16k_mono），
    退出 finally 时随 TemporaryDirectory 一起删除。
    """
    key = _require_valid_key(object_key)
    bucket = _bucket(OSS_INTERNAL_ENDPOINT or OSS_ENDPOINT)
    with tempfile.TemporaryDirectory(prefix="vtt-file-") as tmp:
        target = os.path.join(tmp, "source.bin")
        bucket.get_object_to_file(key, target)
        size = os.path.getsize(target)
        if size == 0:
            raise ParseFailedError("上传的文件没有内容")
        if size > max_bytes:
            raise FileTooLargeError()
        yield target


def delete_object(object_key: str) -> None:
    """删除对象（best-effort）：转写成功后调用；失败只记日志。

    失败任务**不删**：对象保留 ≤1 天（bucket 生命周期规则兜底清理），
    store.retry() 重派作业时还能用同一个 objectKey，用户不用重新上传几百 MB。
    """
    try:
        key = _require_valid_key(object_key)
    except Exception:
        return
    try:
        _bucket(OSS_INTERNAL_ENDPOINT or OSS_ENDPOINT).delete_object(key)
        logger.info("已删除上传对象：%s", key)
    except Exception:  # pragma: no cover - 清理失败不影响任务结果
        logger.warning("删除上传对象失败（留给生命周期规则清理）：%s", key, exc_info=True)