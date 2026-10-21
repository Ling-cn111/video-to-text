"""任务分发抽象（S2-P1）：enqueue 接口 + 多实现，为 Worker 拆分铺路。

设计要点：

1. **作业体自包含**：`TranscribeJob` 携带 pipeline 的全部 6 个入参。
   为什么不是 `enqueue(task_id)`：`url` / `hotwords` / `prefer_subtitles` 并不落在
   `TranscribeTask` 里（任务只存 video / engine），只传 task_id 的话 P3 的 FC
   异步事件拿不到完整作业，只能回读 Redis 补字段——那是把分发层耦合进存储层。
   现在 `job.to_dict()` 就是可直接 `json.dumps` 的事件体，P2 的 Worker 用
   `TranscribeJob.from_dict()` 反解。

2. **模式可切换**（`ENQUEUE_MODE`）：
   - `inline`（默认）：沿用 `BackgroundTasks.add_task`，与改造前逐字节一致
   - `fc` / `mns` / `redis`：P3/P4 的实现位，当前抛 `DispatcherNotImplementedError`
   - 未知值：警告 + 降级 `inline`（与 `_build_store` 同款兜底，避免配错就 500）

3. **为什么保持同步**：路由仍是同步 `def`，`add_task` 注册的也是同步函数，
   Starlette 本来就用线程池执行，改 async 没有收益；而一旦路由改 `async def`，
   `create_task()` 会撞上 P0 的「事件循环内禁止调用桥接」保护，必须连带把
   TaskStore 调用全部改 await——那是 P2 Worker 侧的事，不属于 P1。

4. **调用方式是模块属性访问**（`transcribe.run_transcription_pipeline`）而非
   `from ... import`，保证测试 monkeypatch `app.services.transcribe.*` 依然生效。

分发失败的处理在路由层（把任务标 `failed` 再抛），dispatcher 本身保持纯粹。
"""
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.config import ENQUEUE_MODE
from app.schemas import VideoInfo
from app.services import transcribe

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

logger = logging.getLogger(__name__)


class DispatcherNotImplementedError(NotImplementedError):
    """该分发模式尚未实现（P3 接 FC / P4 接队列）。"""


@dataclass(frozen=True)
class TranscribeJob:
    """一次转写作业的完整入参（可 JSON 序列化，用于跨进程/跨函数投递）。

    字段与 `run_transcription_pipeline` 的签名一一对应，顺序也保持一致，
    inline 分发时直接按位置展开即可，不必维护第二套映射。
    """

    task_id: str
    url: str
    video: VideoInfo | None = None
    prefer_subtitles: bool = True
    hotwords: list[str] | None = None
    engine: str | None = None

    def to_dict(self) -> dict:
        """可 JSON 序列化的作业体（P3 的 FC 事件 payload 即此结构）。"""
        return {
            "task_id": self.task_id,
            "url": self.url,
            "video": self.video.model_dump() if self.video else None,
            "prefer_subtitles": self.prefer_subtitles,
            "hotwords": list(self.hotwords) if self.hotwords else None,
            "engine": self.engine,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TranscribeJob":
        """从事件体反解（P2 Worker 入口用）。缺省字段走默认值，兼容旧事件。"""
        video = data.get("video")
        return cls(
            task_id=str(data["task_id"]),
            url=str(data["url"]),
            video=VideoInfo(**video) if video else None,
            prefer_subtitles=bool(data.get("prefer_subtitles", True)),
            hotwords=data.get("hotwords") or None,
            engine=data.get("engine"),
        )


class Dispatcher(Protocol):
    """分发接口：把作业交给执行方。"""

    mode: str

    def enqueue(self, job: TranscribeJob, background_tasks: "BackgroundTasks | None" = None) -> None: ...


class InlineDispatcher:
    """默认实现：注册到 FastAPI BackgroundTasks（响应返回后在同一进程执行）。

    `background_tasks` 为空时**同步执行**：便于单测与非 HTTP 上下文（脚本/CLI）复用，
    行为等价于直接调用 pipeline。
    """

    mode = "inline"

    def enqueue(self, job: TranscribeJob, background_tasks: "BackgroundTasks | None" = None) -> None:
        args = (job.task_id, job.url, job.video, job.prefer_subtitles, job.hotwords, job.engine)
        if background_tasks is None:
            transcribe.run_transcription_pipeline(*args)
            return
        background_tasks.add_task(transcribe.run_transcription_pipeline, *args)


class _UnimplementedDispatcher:
    """P3/P4 的实现位：显式报错，绝不静默丢任务。"""

    def __init__(self, mode: str, hint: str) -> None:
        self.mode = mode
        self._hint = hint

    def enqueue(self, job: TranscribeJob, background_tasks: "BackgroundTasks | None" = None) -> None:
        raise DispatcherNotImplementedError(
            f"ENQUEUE_MODE={self.mode} 尚未实现（{self._hint}）；请暂时使用 ENQUEUE_MODE=inline"
        )


def _build_dispatcher(mode: str) -> Dispatcher:
    """按 ENQUEUE_MODE 构造分发器；未知值降级 inline 并告警。"""
    normalized = (mode or "inline").strip().lower()
    if normalized == "inline":
        return InlineDispatcher()
    if normalized == "fc":
        return _UnimplementedDispatcher("fc", "P3 接入阿里云 FC 异步调用")
    if normalized == "mns":
        return _UnimplementedDispatcher("mns", "备选方案：MNS 队列触发")
    if normalized == "redis":
        return _UnimplementedDispatcher("redis", "备选方案：Redis 队列 + 定时器消费")
    logger.warning("未知 ENQUEUE_MODE=%r，降级为 inline", mode)
    return InlineDispatcher()


_DISPATCHER: Dispatcher = _build_dispatcher(ENQUEUE_MODE)


def get_dispatcher() -> Dispatcher:
    return _DISPATCHER


def enqueue(job: TranscribeJob, background_tasks: "BackgroundTasks | None" = None) -> None:
    """分发一次转写作业（当前 store / 当前模式的统一入口）。

    异常由调用方（路由）负责兜底：把任务标 failed 再向上抛，
    避免留下永远停在 processing 的孤儿任务。
    """
    _DISPATCHER.enqueue(job, background_tasks)
