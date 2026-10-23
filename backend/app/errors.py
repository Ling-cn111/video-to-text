"""共享异常：由 main.py 的 exception_handler 统一转成 { error: { code, message } }。"""
import re

# ANSI 转义序列（SGR 颜色码等）：yt-dlp / ffmpeg 往 stderr 写日志时会带，
# 若原样拼进 message，前端界面上会显示成 "\u001b[0;31mERROR:\u001b[0m ..." 乱码。
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """剥掉 ANSI 转义序列；非字符串原样返回（容忍 None）。"""
    if not isinstance(text, str):
        return text
    return _ANSI_RE.sub("", text)


class AppException(Exception):
    """业务异常，携带契约错误码与 HTTP 状态码。"""

    def __init__(self, code: str, message: str, status_code: int = 500):
        self.code = code
        # 统一在出口清洗：任何子类、任何来源的 message 都不会把终端颜色码带给前端
        self.message = strip_ansi(message)
        self.status_code = status_code
        super().__init__(self.message)


class InvalidUrlError(AppException):
    def __init__(self, message: str):
        super().__init__(code="INVALID_URL", message=message, status_code=400)


class UnsupportedPlatformError(AppException):
    def __init__(self, message: str):
        super().__init__(code="UNSUPPORTED_PLATFORM", message=message, status_code=422)


class ParseFailedError(AppException):
    def __init__(self, message: str):
        super().__init__(code="INTERNAL_ERROR", message=message, status_code=500)


class TaskNotFoundError(AppException):
    def __init__(self, message: str = "任务不存在或已过期，请重新解析视频链接"):
        super().__init__(code="INVALID_TASK", message=message, status_code=404)


class CloudNotConfiguredError(AppException):
    """选择了云端引擎但 CLOUD_ASR_API_KEY 未配置：提交即报错，不静默降级。"""

    def __init__(self, message: str = "云端转写未配置：请先在后端设置 CLOUD_ASR_API_KEY（默认指向硅基流动），或使用本地模式"):
        super().__init__(code="CLOUD_NOT_CONFIGURED", message=message, status_code=400)


class SummarizeNotConfiguredError(AppException):
    """请求 AI 总结但 LLM_API_KEY 未配置：提交即报错，不静默降级。"""

    def __init__(self, message: str = "AI 总结未配置：请先在后端设置 DEEPSEEK_API_KEY 或 DASHSCOPE_API_KEY（LLM_PROVIDER 切换厂商）"):
        super().__init__(code="SUMMARIZE_NOT_CONFIGURED", message=message, status_code=400)


class TranscriptTooLongError(AppException):
    """总结请求的文字稿超过长度上限。"""

    def __init__(self, message: str = "文字稿过长（超过 10 万字），暂不支持生成 AI 总结"):
        super().__init__(code="TRANSCRIPT_TOO_LONG", message=message, status_code=413)


class AudioTooLargeError(AppException):
    """音频超过大小上限（MAX_AUDIO_SIZE_MB）：拒绝整块载入内存，避免 OOM。"""

    def __init__(self, message: str = "音频过大，暂不支持"):
        super().__init__(code="AUDIO_TOO_LARGE", message=message, status_code=413)


class SummarizeFailedError(AppException):
    """LLM 调用失败（网络/HTTP/重试耗尽）。"""

    def __init__(self, message: str = "AI 总结生成失败，请稍后重试"):
        super().__init__(code="SUMMARIZE_FAILED", message=message, status_code=502)
