"""转写路由：POST 创建任务（分发执行）+ GET 轮询详情。

分发由 `services/dispatcher` 决定（ENQUEUE_MODE）：默认 inline 仍走
BackgroundTasks，与 S2-P1 之前逐字节一致；后续阶段可切到 FC 异步调用。

M3 起按 kind 分流：url（默认，链接解析）| file（本地上传，对象已在 OSS）。
file 来源不查平台白名单、不走短链展开（没有 URL 语义），改为复核对象存在性
与大小——这是「声明（upload/init）/ HEAD（本处）/ 流式（Worker）」三重
大小校验的第二重。
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Header

from app.config import CLOUD_ASR_API_KEY, FILE_MAX_SIZE_MB
from app.errors import (
    CloudNotConfiguredError,
    FileTooLargeError,
    InvalidUrlError,
    TaskNotFoundError,
    UploadedFileNotFoundError,
    UnsupportedPlatformError,
)
from app.platforms import detect_platform
from app.schemas import FileRef, StartTaskResponse, StartTranscribeRequest, TaskDetailResponse, VideoInfo
from app.services import storage
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


def _resolve_file_ref(payload: StartTranscribeRequest) -> tuple[FileRef, VideoInfo]:
    """校验 file 来源任务的文件引用，返回 (引用, 合成的展示用 VideoInfo)。

    - 未配置 OSS → FILE_STORAGE_NOT_CONFIGURED（正常流程前端已按 capabilities
      隐藏入口，这条兜住直调 API 的情形）
    - objectKey 按 uploads/{uuid}/{文件名} 白名单复核 → 不合法一律视为
      「文件未就绪」（FILE_NOT_FOUND）：伪造/过期引用与「还没传完就提交」
      对用户是同一个动作——重新上传
    - HEAD 对象拿实际大小：不存在 → FILE_NOT_FOUND；超 FILE_MAX_SIZE_MB →
      FILE_TOO_LARGE。这是三重大小校验的第二重（Worker 流式校验是第三重）
    """
    storage.require_file_storage()
    file_ref = payload.file
    if file_ref is None or not (file_ref.objectKey or "").strip():
        raise UploadedFileNotFoundError()

    try:
        storage.checked_key(file_ref.objectKey)
    except Exception:
        raise UploadedFileNotFoundError()

    size = storage.head_size(file_ref.objectKey)
    if size is None:
        raise UploadedFileNotFoundError()
    if size > FILE_MAX_SIZE_MB * 1024 * 1024:
        raise FileTooLargeError(f"文件超过 {FILE_MAX_SIZE_MB}MB 上限，请压缩或裁剪后再上传")

    video = payload.video or VideoInfo(
        title=file_ref.fileName or "本地视频",
        cover="",
        duration=0,  # 真实时长由 Worker 转码后按 WAV 字节数回填
        platform="local",
        videoId=file_ref.objectKey,
    )
    return file_ref, video


@router.post("/api/transcribe", response_model=StartTaskResponse)
def create_transcription(
    payload: StartTranscribeRequest,
    background_tasks: BackgroundTasks,
    x_vtt_asr_key: str | None = Header(default=None, alias=HEADER_ASR_KEY),
) -> StartTaskResponse:
    byok_key = normalize_key(x_vtt_asr_key)
    engine = _resolve_engine(payload, byok_key)

    if payload.kind == "file":
        file_ref, video = _resolve_file_ref(payload)
        task_id = create_task(video, engine, source="file", file=file_ref.model_dump())
        job = TranscribeJob(
            task_id=task_id,
            url=None,
            video=video,
            prefer_subtitles=False,  # 本地文件没有视频页 URL，字幕快路径不适用
            hotwords=payload.hotwords,
            engine=engine,
            byok_key=byok_key,
            source="file",
            file=file_ref,
        )
        response_stage = "extract_audio"
    else:
        url = _resolve_target_url(payload)
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
        response_stage = "parse_link"

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
    return StartTaskResponse(taskId=task_id, status="processing", stage=response_stage, progress=0)


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
