"""转写任务注册表（内存实现）。

阶段一为单进程本地部署，任务存于进程内存即可满足验收；
多 worker / 云端部署需换 Redis 等外部队列，接口（create/get/update）保持不变。
"""
import threading
import time
import uuid
from dataclasses import dataclass, field

from app.schemas import TranscriptItem, VideoInfo

_TASKS: dict[str, "TranscribeTask"] = {}
_LOCK = threading.Lock()


@dataclass
class TranscribeTask:
    task_id: str
    status: str = "processing"  # processing | completed | failed
    stage: str = "parse_link"  # parse_link | extract_audio | asr
    progress: int = 0
    video: VideoInfo | None = None
    transcript: list[TranscriptItem] = field(default_factory=list)
    plain_text: str | None = None
    error: dict | None = None  # { code, message }
    created_at: float = field(default_factory=time.time)


def create_task(video: VideoInfo | None) -> str:
    task_id = uuid.uuid4().hex
    with _LOCK:
        _TASKS[task_id] = TranscribeTask(task_id=task_id, video=video)
    return task_id


def get_task(task_id: str) -> TranscribeTask | None:
    with _LOCK:
        return _TASKS.get(task_id)


def update_task(task_id: str, **fields) -> None:
    with _LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            return
        for key, value in fields.items():
            setattr(task, key, value)


def set_stage(task_id: str, stage: str, progress: int) -> None:
    update_task(task_id, stage=stage, progress=progress)
