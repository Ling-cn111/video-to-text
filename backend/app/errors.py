"""共享异常：由 main.py 的 exception_handler 统一转成 { error: { code, message } }。"""


class AppException(Exception):
    """业务异常，携带契约错误码与 HTTP 状态码。"""

    def __init__(self, code: str, message: str, status_code: int = 500):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


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
