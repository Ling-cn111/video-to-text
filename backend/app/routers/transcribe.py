"""转写路由：POST 创建任务（分发执行）+ GET 轮询详情。

分发由 `services/dispatcher` 决定（ENQUEUE_MODE）：默认 inline 仍走
BackgroundTasks，与 S2-P1 之前逐字节一致；后续阶段可切到 FC 异步调用。
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Header

from app.config import CLOUD_ASR_API_KEY
from app.errors import CloudNotConfiguredError, InvalidUrlError, TaskNotFoundError, UnsupportedPlatformError
from app.platforms import detect_platform
from app.schemas import StartTaskResponse, StartTranscribeRequest, TaskDetailResponse
from app.services.asr import local_engine_available
from app.services.byok import HEADER_ASR_KEY, normalize_key
from app.services.dispatcher import TranscribeJob, enqueue
from app.services import short_link
from app.services.tasks import create_task, get_task, update_task
from app.url_guard import validate_public_http_url

logger = logging.getLogger(__name__)

router = APIRouter()

# 平台 → 视频页 URL 模板（videoId 重建链接用）
_URL_TEMPLATES = {"bilibili": "https://www.bilibili.com/video/{videoId}"}


def _resolve_engine(payload: StartTranscribeRequest, byok_key: str | None = None) -> str:
    """解析本次任务的 ASR 引擎。

    两种情形：

    1. 请求显式带了 engine（本地自建部署仍保留「本地 / 云端」选择器，见
       capabilities.localAsrAvailable）：照它说的办，云端还要预检 Key。
    2. 请求没带 engine（网页端与桌面端已隐藏该选择器）：按能力自动挑，即
       「能用哪个就用哪个」——有云端 Key 走云端（快、准），没有就退本地
       （免费、慢），两个都没有才报错。降级顺序由后端决定而不是前端猜。

    不自动挑的后果很具体：线上 FC 镜像没有 faster-whisper，若前端仍发
    engine=local，用户会在等满 40 秒后收到一句没有信息量的「转写失败」。
    """
    if payload.engine:
        if payload.engine == "cloud" and not CLOUD_ASR_API_KEY and not byok_key:
            raise CloudNotConfiguredError()
        return payload.engine

    if CLOUD_ASR_API_KEY or byok_key:
        return "cloud"
    if local_engine_available():
        return "local"
    # 两个都没有：说清楚缺什么，而不是让用户去猜
    raise CloudNotConfiguredError()


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

    # 分享短链在这里也展开一次。`/api/parse` 已经展开过，前端会用解析出的
    # videoId 重建 canonical 链接，正常走不到这里；但任务可能被直接创建
    # （脚本、重试、第三方调用），把短链原样存进任务记录会让 pipeline 里的
    # 字幕/playurl 快路径全部失效。展开是幂等的，代价只有一次 HEAD/GET。
    if short_link.is_short_link(candidate):
        expanded = short_link.expand_short_link(candidate)
        if expanded:
            candidate = expanded
            parsed = validate_public_http_url(candidate)
            if detect_platform(parsed.hostname or "") is None:
                raise UnsupportedPlatformError(
                    f"短链指向了不受支持的地址，当前支持哔哩哔哩"
                )
    return candidate


@router.post("/api/transcribe", response_model=StartTaskResponse)
def create_transcription(
    payload: StartTranscribeRequest,
    background_tasks: BackgroundTasks,
    x_vtt_asr_key: str | None = Header(default=None, alias=HEADER_ASR_KEY),
) -> StartTaskResponse:
    url = _resolve_target_url(payload)
    byok_key = normalize_key(x_vtt_asr_key)
    engine = _resolve_engine(payload, byok_key)
    task_id = create_task(payload.video, engine)
    job = TranscribeJob(
        task_id=task_id,
        url=url,
        video=payload.video,
        prefer_subtitles=True,
        hotwords=payload.hotwords,
        engine=engine,
        byok_key=byok_key,
    )
    try:
        enqueue(job, background_tasks=background_tasks)
    except Exception:
        # 分发失败（fc 投递被拒 / 网络不通 / mns|redis 尚未实现）：把任务落到 failed，
        # 避免留下永远停在 processing 的孤儿任务被前端无限轮询。
        logger.error("任务分发失败（task_id=%s, job=%s）", task_id, job, exc_info=True)
        update_task(
            task_id,
            status="failed",
            error={"code": "TRANSCRIBE_FAILED", "message": "任务分发失败，请重试"},
        )
        raise
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
        transcriptSource=task.transcript_source,
    )
