"""重点帧抽帧路由（PR #89）：POST /api/frames 创建抽帧作业。

语义要点：

- 只接受已 completed 的 URL 任务（file 任务 v1 不支持 → FRAMES_UNSUPPORTED；
  非 B站链接的任务在 Worker 侧同样会被拒，路由层先拦一层给明确报错）
- **幂等**：frames 已在 processing / ready 时直接返回现状，不重复派发——
  重复点击与前端重试都安全；failed 允许重新派发（覆盖旧状态重试）
- 时间戳不信任客户端原始值：sanitize_timestamps 统一钳制（时长内、2s 去重、
  截断 FRAMES_MAX_COUNT）
- URL 不落任务库：按任务的 videoId 重建 canonical B站链接（与 /api/transcribe
  的缺省重建逻辑同源），派发给 Worker
- 分发失败 → frames.status=failed 再向上抛（异步作业的失败收敛进任务详情，
  前端轮询可见；与转写路由的孤儿任务兜底同一手法）
"""
import logging

from fastapi import APIRouter, BackgroundTasks

from app.config import FRAMES_MAX_COUNT
from app.errors import (
    AppException,
    FramesTaskNotReadyError,
    FramesUnsupportedError,
    InvalidUrlError,
    TaskNotFoundError,
)
from app.schemas import FramesStartRequest, FramesStartResponse
from app.services import frames as frames_service
from app.services import storage
from app.services.dispatcher import FramesJob, enqueue_frames
from app.services.tasks import get_task, update_task
from app.url_guard import validate_public_http_url

logger = logging.getLogger(__name__)

router = APIRouter()

# 平台 → 视频页 URL 模板：与 routers/transcribe.py 的 _URL_TEMPLATES 同源
# （videoId 是解析阶段归一的 BV 号，重建出来的是 canonical 链接，非短链）
_BILI_URL_TEMPLATE = "https://www.bilibili.com/video/{videoId}"


@router.post("/api/frames", response_model=FramesStartResponse)
def create_frames_job(
    payload: FramesStartRequest, background_tasks: BackgroundTasks
) -> FramesStartResponse:
    task = get_task(payload.taskId)
    if task is None:
        raise TaskNotFoundError()

    current = task.frames or {}
    if current.get("status") in ("processing", "ready"):
        # 幂等：抽帧已在进行 / 已就绪，重复点击不重复派发（不重复计费）。
        # ready 时现场签发预签名 URL，前端拿到即可直接渲染。
        return FramesStartResponse(
            taskId=task.task_id, frames=frames_service.frames_to_out(current, presign=True)
        )

    if task.status != "completed":
        raise FramesTaskNotReadyError()

    if task.source == "file" or (task.video is not None and task.video.platform == "local"):
        # 文件任务的源对象在转写成功后即删（成功即删），二次抽帧没有源可用
        raise FramesUnsupportedError("本地文件任务暂不支持生成重点帧")

    video_id = (task.video.videoId or "").strip() if task.video is not None else ""
    if not video_id:
        raise FramesUnsupportedError("任务缺少视频标识，无法定位源视频")

    storage.require_file_storage()  # 未配 OSS → FILE_STORAGE_NOT_CONFIGURED

    url = _BILI_URL_TEMPLATE.format(videoId=video_id)
    try:
        validate_public_http_url(url)
    except InvalidUrlError as exc:
        # videoId 理论上来自解析阶段的 BV 归一，这里是防御纵深
        raise FramesUnsupportedError(f"任务的视频链接不合法：{exc.message}") from exc

    duration = task.video.duration if task.video is not None else 0
    points = frames_service.sanitize_timestamps(payload.timestamps, duration, FRAMES_MAX_COUNT)
    if not points:
        raise AppException("FRAMES_FAILED", "没有有效的抽帧时间点", 422)

    frames_state = {"status": "processing", "error": None, "items": []}
    # 终态可变字段写入（TERMINAL_MUTABLE_FIELDS 例外）：先标 processing 再派发，
    # Worker 的幂等检查才能识别「已在处理」
    update_task(task.task_id, frames=frames_state)

    job = FramesJob(task_id=task.task_id, url=url, timestamps=points)
    try:
        enqueue_frames(job, background_tasks=background_tasks)
    except Exception:
        logger.error("抽帧作业分发失败（task_id=%s）", task.task_id, exc_info=True)
        update_task(
            task.task_id,
            frames={
                "status": "failed",
                "error": {"code": "FRAMES_FAILED", "message": "重点帧任务分发失败，请重试"},
                "items": [],
            },
        )
        raise
    return FramesStartResponse(
        taskId=task.task_id, frames=frames_service.frames_to_out(frames_state)
    )
