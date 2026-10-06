"""Pydantic 契约模型：与前端 src/lib/types.ts 保持一致（camelCase 字段）。"""
from typing import Literal

from pydantic import BaseModel, Field


class ParseRequest(BaseModel):
    url: str


class VideoInfo(BaseModel):
    """对应前端 types.ts 的 VideoInfo"""

    title: str
    cover: str
    duration: int
    platform: str
    videoId: str


class TranscriptItem(BaseModel):
    """对应前端 types.ts 的 TranscriptItem；time 为相对视频开头的秒数"""

    time: float
    text: str


class StartTranscribeRequest(BaseModel):
    """POST /api/transcribe 请求体。

    - videoId 必填；url 缺省时由 videoId 按平台规则重建
    - video 为解析阶段透传的元数据（供前端刷新恢复展示）
    - hotwords 为可选热词（专有名词列表，经 initial_prompt 引导 ASR 识别）
    - engine 为可选转写引擎手动选择：local（本地 faster-whisper）/ cloud（云端 OpenAI 兼容）；
      缺省跟随后端 ASR_ENGINE 环境变量。纯手动选择，无自动切换
    """

    videoId: str
    url: str | None = None
    video: VideoInfo | None = None
    hotwords: list[str] | None = None
    engine: Literal["local", "cloud"] | None = None


class StartTaskResponse(BaseModel):
    taskId: str
    status: str
    stage: str
    progress: int


class AppErrorBody(BaseModel):
    """所有非 2xx 响应统一为 { error: { code, message } }，与前端 ApiErrorBody 一致"""

    code: str
    message: str


class ErrorResponse(BaseModel):
    error: AppErrorBody


class TaskDetailResponse(BaseModel):
    """GET /api/transcribe/{taskId} 响应（前端 GetTaskResponse 的后端实现）"""

    taskId: str
    status: str
    stage: str
    progress: int
    video: VideoInfo | None = None
    transcript: list[TranscriptItem] = Field(default_factory=list)
    plainText: str | None = None
    error: AppErrorBody | None = None
    # 文字稿来源：subtitle_cc（B站 CC 字幕）/ subtitle_ai（B站 AI 字幕）/ asr（语音识别）
    transcriptSource: str | None = None
