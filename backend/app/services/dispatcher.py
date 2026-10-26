"""任务分发抽象（S2-P1/P3）：enqueue 接口 + 多实现，为 Worker 拆分铺路。

设计要点：

1. **作业体自包含**：`TranscribeJob` 携带 pipeline 的全部 6 个入参。
   为什么不是 `enqueue(task_id)`：`url` / `hotwords` / `prefer_subtitles` 并不落在
   `TranscribeTask` 里（任务只存 video / engine），只传 task_id 的话 P3 的 FC
   异步事件拿不到完整作业，只能回读 Redis 补字段——那是把分发层耦合进存储层。
   现在 `job.to_dict()` 就是可直接 `json.dumps` 的事件体，P2 的 Worker 用
   `TranscribeJob.from_dict()` 反解。

2. **模式可切换**（`ENQUEUE_MODE`）：
   - `inline`（默认）：沿用 `BackgroundTasks.add_task`，与改造前逐字节一致
   - `fc`：投递到 FC worker 函数的 HTTP 触发器（`X-Fc-Invocation-Type: Async`）
   - `mns` / `redis`：P4 的实现位，当前抛 `DispatcherNotImplementedError`
   - 未知值：警告 + 降级 `inline`（与 `_build_store` 同款兜底，避免配错就 500）

3. **为什么保持同步**：路由仍是同步 `def`，`add_task` 注册的也是同步函数，
   Starlette 本来就用线程池执行，改 async 没有收益；而一旦路由改 `async def`，
   `create_task()` 会撞上 P0 的「事件循环内禁止调用桥接」保护，必须连带把
   TaskStore 调用全部改 await——那是 P2 Worker 侧的事，不属于 P1。
   `fc` 模式同理：`httpx.Client` 同步发一个只等 202 的短请求，阻塞可忽略。

4. **调用方式是模块属性访问**（`transcribe.run_transcription_pipeline`）而非
   `from ... import`，保证测试 monkeypatch `app.services.transcribe.*` 依然生效。

分发失败的处理在路由层（把任务标 `failed` 再抛），dispatcher 本身保持纯粹。
"""
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import httpx

from app.config import ASYNC_TIMEOUT_SECONDS, ENQUEUE_MODE, WORKER_PATH, WORKER_TOKEN, WORKER_URL
from app.schemas import VideoInfo
from app.services import transcribe

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

logger = logging.getLogger(__name__)


class DispatcherNotImplementedError(NotImplementedError):
    """该分发模式尚未实现（P4 接 MNS / Redis 队列）。"""


class DispatchError(RuntimeError):
    """分发失败：配置缺失 / 网络错误 / 平台拒收。

    与 DispatcherNotImplementedError 分开：前者是「配错了/环境不通」，后者是
    「这个模式还没写」。路由层对两者的处理相同（标 failed + 500），但日志与
    排查方向完全不同。
    """


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
    # 用户自带的云端 ASR Key（见 services/byok.py）。跨进程投递必须随作业走，
    # 否则 Worker 拿不到；但**不进任务库**（tasks._payload_dict 不含该字段），
    # 只在「API 函数 → Worker 函数」这一跳的 HTTPS 载荷里存在。
    byok_key: str | None = None

    def to_dict(self) -> dict:
        """可 JSON 序列化的作业体（P3 的 FC 事件 payload 即此结构）。"""
        return {
            "task_id": self.task_id,
            "url": self.url,
            "video": self.video.model_dump() if self.video else None,
            "prefer_subtitles": self.prefer_subtitles,
            "hotwords": list(self.hotwords) if self.hotwords else None,
            "engine": self.engine,
            "byok_key": self.byok_key,
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
            byok_key=data.get("byok_key") or None,
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
        # byok_key 用关键字传：它是 4 个位置参数之后的第 7 个形参，插在 on_heartbeat
        # 之后，位置传递一旦漏填 None 就会把 Key 当成心跳回调传进去。
        args = (job.task_id, job.url, job.video, job.prefer_subtitles, job.hotwords, job.engine)
        kwargs = {"byok_key": job.byok_key}
        if background_tasks is None:
            transcribe.run_transcription_pipeline(*args, **kwargs)
            return
        background_tasks.add_task(transcribe.run_transcription_pipeline, *args, **kwargs)


class FcDispatcher:
    """阿里云 FC 异步调用：POST 到 worker 函数的 HTTP 触发器。

    为什么走「HTTP 触发器 + X-Fc-Invocation-Type: Async」而不是 OpenAPI
    `InvokeFunction`：官方文档三处明确 HTTP 触发器支持请求级异步（见
    docs/s2-async-recon.md §8）。这样**不需要 ACS3-HMAC-SHA256 签名、不需要
    AK/SK、不需要 SDK**，凭据面最小。

    语义要点：
    - FC 收到异步请求后立即返回 **202** 并落内部队列，本方法不等待作业完成；
    - 因此 `background_tasks` 参数被忽略（异步由平台队列承担，不再需要
      同进程后台任务）；
    - 失败不重试：抛 DispatchError，由路由层标 failed + 500 让用户手动重试。
      静默重试会与 FC 自身的 3 次重试叠加，放大重复计费风险。
    """

    mode = "fc"

    def __init__(
        self,
        url: str,
        path: str = WORKER_PATH,
        token: "str | None" = None,
        timeout: float = ASYNC_TIMEOUT_SECONDS,
        transport: "httpx.BaseTransport | None" = None,
    ) -> None:
        self._url = (url or "").strip()
        self._path = (path or "").strip()
        # None = 未指定，调用时读模块级 WORKER_TOKEN（便于测试 monkeypatch，也与
        # worker_app 侧的取值方式一致）；显式传 "" 表示「本次不带 token」。
        self._token = token
        self._timeout = timeout
        # 单测注入 MockTransport；生产为 None（httpx 自建连接池）
        self._transport = transport

    @property
    def endpoint(self) -> str:
        """拼出完整投递地址（容忍配置里多余的/缺失的斜杠）。"""
        base = self._url.rstrip("/")
        if not self._path:
            return base
        return f"{base}/{self._path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            # 关键：FC 收到后立即回 202，把事件落内部队列，不再受调用方生命周期约束
            "X-Fc-Invocation-Type": "Async",
        }
        token = WORKER_TOKEN if self._token is None else self._token
        if token:
            # P2 的 Worker 端点校验；同时兼容将来把触发器改成 Bearer 认证
            headers["X-Worker-Token"] = token
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def enqueue(self, job: TranscribeJob, background_tasks: "BackgroundTasks | None" = None) -> None:
        if not self._url:
            raise DispatchError("ENQUEUE_MODE=fc 但未配置 WORKER_URL（worker 函数的 HTTP 触发器地址）")

        # 用 content= 而非 json=：字节与签名哈希一一对应，将来若切 OpenAPI 可直接复用
        body = json.dumps(job.to_dict(), ensure_ascii=False)
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.post(self.endpoint, content=body, headers=self._headers())
        except UnicodeEncodeError as exc:
            # HTTP 头只允许 ASCII：WORKER_TOKEN 里混进中文/emoji 时 httpx 直接抛，
            # 不捕获的话会变成满屏 500，看不出是配置问题
            raise DispatchError(
                f"FC 投递失败：请求头含非 ASCII 字符，请检查 WORKER_TOKEN（{exc}）"
            ) from exc
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise DispatchError(f"FC 投递失败（{self.endpoint}）：{exc}") from exc

        # 202 = 异步受理（标准）；200 = 直连 worker 同步跑完（本地联调/兜底）
        if response.status_code not in (200, 202):
            detail = response.text[:200].replace("\n", " ")
            raise DispatchError(
                f"FC 投递被拒（HTTP {response.status_code}）：{detail}"
            )
        logger.info(
            "已投递到 FC（task_id=%s, request_id=%s, status=%s）",
            job.task_id,
            response.headers.get("X-Fc-Request-Id", "-"),
            response.status_code,
        )


class _UnimplementedDispatcher:
    """P4 的实现位：显式报错，绝不静默丢任务。"""

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
        return FcDispatcher(WORKER_URL)
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
