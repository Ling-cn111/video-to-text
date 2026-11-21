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


class ByokKeyInvalidError(AppException):
    """用户自带的 Key 被上游拒绝（401/403）。

    与 `SummarizeNotConfiguredError` 分开：那条是「站点没配 Key，请联系部署者」，
    这条是「你填的 Key 不对，请改你自己的」——动作主体不同，合并会让用户找错人。
    """

    def __init__(self, message: str = "你填写的 API Key 无效或已过期，请在右上角「API 设置」中更换"):
        super().__init__(code="BYOK_KEY_INVALID", message=message, status_code=401)


class ByokEndpointInvalidError(AppException):
    """用户自填的接口地址 / 模型名不合规，或与 Key 搭配不完整。

    为什么单独一个码而不是复用 INVALID_URL：这条错误的动作主体是用户自己
    （去「API 设置」改地址），而 INVALID_URL 指的是「要转写的视频链接」，
    两者在同一个会话里可能同时出现，混用会让人不知道该改哪一个。
    """

    def __init__(self, message: str = "接口地址不可用，请在「API 设置」中检查"):
        super().__init__(code="BYOK_ENDPOINT_INVALID", message=message, status_code=400)


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


class FileTooLargeError(AppException):
    """上传的源文件超过大小上限（FILE_MAX_SIZE_MB）。

    与 AudioTooLargeError（413，音频超 MAX_AUDIO_SIZE_MB）分开：一个是「上传的
    源视频太大」（前端预校验 + 后端三重校验），一个是「转码后的音频太大」——
    一个 500MB 的源文件完全可能因为太长在 extract_audio 阶段撞上后者。
    """

    def __init__(self, message: str = "文件过大，请上传 500MB 以内的视频"):
        super().__init__(code="FILE_TOO_LARGE", message=message, status_code=413)


class FileFormatUnsupportedError(AppException):
    """上传的文件格式不受支持（扩展名白名单外）。

    与 UnsupportedPlatformError（422，链接指向的平台不支持）对齐状态码：都是
    「请求本体合法但能力范围外」，且两者动作主体都是用户换一个输入。
    """

    def __init__(self, message: str = "暂不支持该文件格式，请上传 mp4 / mov / webm 视频"):
        super().__init__(code="FILE_FORMAT_UNSUPPORTED", message=message, status_code=422)


class FileStorageNotConfiguredError(AppException):
    """站点未配置 OSS（OSS_BUCKET 等）：本地上传功能整体不可用。

    与 CloudNotConfiguredError 同型：「未配置的对应功能明确提示」，前端通过
    /api/capabilities 的 fileUploadConfigured 预先隐藏入口，这条是兜底。
    """

    def __init__(self, message: str = "本地上传未开启：站点未配置对象存储（OSS_BUCKET 等）"):
        super().__init__(code="FILE_STORAGE_NOT_CONFIGURED", message=message, status_code=400)


class UploadedFileNotFoundError(AppException):
    """转写请求引用的 OSS 对象不存在（预签名上传未完成就发起转写 / 对象已被清理）。

    为什么不复用 INVALID_TASK：那条说的是「taskId 查无此任务」，这条说的是
    「上传凭证指向的文件还没到位」，用户该做的动作不同（等上传完成 / 重新上传）。
    """

    def __init__(self, message: str = "上传的文件还未就绪，请等上传完成后再发起转写"):
        super().__init__(code="FILE_NOT_FOUND", message=message, status_code=404)
