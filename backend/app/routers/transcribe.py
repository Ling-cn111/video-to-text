"""转写路由：POST 创建任务（BackgroundTasks 异步执行）+ GET 轮询详情。"""
from fastapi import APIRouter, BackgroundTasks

from app.config import ASR_ENGINE, CLOUD_ASR_API_KEY
from app.errors import CloudNotConfiguredError, InvalidUrlError, TaskNotFoundError, UnsupportedPlatformError
from app.platforms import detect_platform
from app.schemas import StartTaskResponse, StartTranscribeRequest, TaskDetailResponse
from app.services.tasks import create_task, get_task
from app.services.transcribe import run_transcription_pipeline
from app.url_guard import validate_public_http_url

router = APIRouter()

# 平台 → 视频页 URL 模板（videoId 重建链接用）
_URL_TEMPLATES = {"bilibili": "https://www.bilibili.com/video/{videoId}"}


def _resolve_engine(payload: StartTranscribeRequest) -> str:
    """解析本次任务的 ASR 引擎：请求字段优先，缺省跟随后端 ASR_ENGINE 环境变量。

    云端模式在提交时预检 Key：未配置直接报 CLOUD_NOT_CONFIGURED（明确中文提示），
    不创建任务、不静默降级；运行期云端故障仍由 ASR_ENGINE_FALLBACK 降级本地。
    """
    engine = payload.engine or ASR_ENGINE
    if engine == "cloud" and not CLOUD_ASR_API_KEY:
        raise CloudNotConfiguredError()
    return engine


def _resolve_target_url(payload: StartTranscribeRequest) -> str:
    """校验并返回目标视频页 URL。url 缺省时按平台模板由 videoId 重建。"""
    video_id = (payload.videoId or "").strip()
    if not video_id:
        raise InvalidUrlError("缺少 videoId，请先解析视频链接")

    candidate = (payload.url or "").strip()
    if not candidate:
        platform = payload.video.platform if payload.video else "bilibili"
        template = _URL_TEMPLATES.get(platform)
        if template is None:
            raise UnsupportedPlatformError("暂不支持该平台，当前支持哔哩哔哩")
        candidate = template.format(videoId=video_id)

    parsed = validate_public_http_url(candidate)
    meta = detect_platform(parsed.hostname or "")
    if meta is None:
        raise UnsupportedPlatformError(
            f"暂不支持「{parsed.hostname}」平台，当前支持哔哩哔哩"
        )
    return candidate


@router.post("/api/transcribe", response_model=StartTaskResponse)
def create_transcription(payload: StartTranscribeRequest, background_tasks: BackgroundTasks) -> StartTaskResponse:
    url = _resolve_target_url(payload)
    engine = _resolve_engine(payload)
    task_id = create_task(payload.video, engine)
    background_tasks.add_task(
        run_transcription_pipeline, task_id, url, payload.video, True, payload.hotwords, engine
    )
    return StartTaskResponse(taskId=task_id, status="processing", stage="parse_link", progress=0)


@router.get("/api/transcribe/{task_id}", response_model=TaskDetailResponse)
def get_transcription(task_id: str) -> TaskDetailResponse:
    task = get_task(task_id)
    if task is None:
        raise TaskNotFoundError()
    return TaskDetailResponse(
        taskId=task.task_id,
        status=task.status,
        stage=task.stage,
        progress=task.progress,
        video=task.video,
        transcript=task.transcript,
        plainText=task.plain_text,
        error=task.error,
    )
