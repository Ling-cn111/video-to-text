"""Pydantic 契约模型：与前端 src/lib/types.ts 保持一致（camelCase 字段）。"""
from pydantic import BaseModel


class ParseRequest(BaseModel):
    url: str


class VideoInfo(BaseModel):
    """对应前端 types.ts 的 VideoInfo"""

    title: str
    cover: str
    duration: int
    platform: str
    videoId: str


class AppErrorBody(BaseModel):
    """所有非 2xx 响应统一为 { error: { code, message } }，与前端 ApiErrorBody 一致"""

    code: str
    message: str


class ErrorResponse(BaseModel):
    error: AppErrorBody
