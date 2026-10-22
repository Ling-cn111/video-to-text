"""独立 Worker ASGI 应用（S2-P2）：Serverless 异步执行入口。

与 API 应用（app/main.py）完全隔离：
- 不挂 RateLimitMiddleware / CORS（Worker 由平台异步调用，不是公网接口）
- 不 import app.main → 不会触发 API 侧 init（recover）把别的实例在途任务误杀
- 初始化显式 `init_task_store(recover_interrupted=False)`，再叠加部署侧
  `TASK_INIT_MODE=none` 双保险

请求体约定（不依赖任何特定 Header 定位 task_id）：
- 裸作业体：`{"task_id": ..., "url": ...}`（TranscribeJob.to_dict()）
- 包装体：`{"body": "<json 字符串>"}` / `{"body": {...}}` / `{"payload": ...}`
- 网关风格：`{"body": "<base64>", "isBase64Encoded": true}`

两个入口（S2-P3 补第二个，S2-P4 加 timer 分流）：
- `POST /internal/worker/transcribe`：HTTP 触发器 + `X-Fc-Invocation-Type: Async`
  异步调用时 FC 透传原始请求，这是主路径；
- `POST /invoke`：`InvokeFunction` API 调用与**所有事件源触发器（含定时触发器）**
  的固定落点，FC 把 event 作为 body POST 到这里（Content-Type: application/octet-stream）。

S2-P4 定时触发器分流（关键约束）：
阿里云 FC 的定时触发器是**事件调用**，不能指定 HTTP 路径——FC 固定投递到 `/invoke`。
因此 reaper 不能只挂在 `/internal/worker/reap` 上，否则线上永远不会被触发。
`/invoke` 按事件特征分流，判定顺序固定为：
1. 含 `triggerTime` + `triggerName` → 定时事件，跑 reaper
2. 含 `task_id` → 转写作业
3. 其它 → 422
Timer 必须优先：定时事件体里的 `payload` 是用户自定义字符串，理论上可能被配成
一个恰好含 `task_id` 的 JSON，先判 timer 特征可避免误当作业执行。

`/internal/worker/reap` 保留给本地脚本与人工 curl（Timer 打不到它）。

执行语义：
1. 任务不存在 / 非 processing → 200 skipped（确定性结果，不触发平台重试）
2. `SET NX EX` 抢锁失败 → 200 skipped（另一实例在处理）
3. pipeline 业务异常 → pipeline 内部已收敛为 failed，返回 200（重试无意义且会重复计费）
4. 胶水层/未捕获异常 → 标 failed 后返回 5xx（基础设施类问题，值得平台重试）
5. 定时触发器事件 → 跑 reaper，返回收割计数（与作业路径完全隔离）
"""
import asyncio
import base64
import binascii
import json
import logging
import secrets
from typing import Any, Callable

from fastapi import Body, FastAPI, Header, HTTPException, Request

from app.config import TASK_LOCK_SECONDS, WORKER_TOKEN
from app.services import reaper
from app.services import tasks as task_store
from app.services import transcribe
from app.services.dispatcher import TranscribeJob

logger = logging.getLogger(__name__)

# Worker 路由（HTTP 触发器异步调用，主路径）
WORKER_PATH = "/internal/worker/transcribe"

# InvokeFunction API / 事件源触发器（含定时触发器）的固定落点
INVOKE_PATH = "/invoke"

# 僵尸任务清理入口（S2-P4）：本地脚本 / 人工 curl 用，定时触发器打不到这里
REAP_PATH = "/internal/worker/reap"

# 解包最多下钻层数（防畸形事件无限递归）
_MAX_UNWRAP_DEPTH = 4

# 任务不存在时的 skipped 原因
_SKIP_NOT_FOUND = "task_not_found"

app = FastAPI(title="video-to-text worker", version="0.1.0")

_initialized = False


async def _ensure_initialized() -> None:
    """懒初始化任务存储：不做重启恢复（recover_interrupted=False）。

    不能调同步版 `init_task_store()`——它在事件循环内会撞上 P0 的桥接保护，
    这里直接 await store.init()。

    放在请求入口而不是模块导入：避免与测试/同进程多应用场景的导入顺序耦合
    （谁先 init，谁的 recover 语义就生效）。生产上 API 与 Worker 是独立进程。
    """
    global _initialized
    if _initialized:
        return
    store = task_store.get_task_store()
    init = getattr(store, "init", None)
    if init is not None:
        await init(recover_interrupted=False)
    _initialized = True


def _token_matches(candidate: str | None) -> bool:
    """常量时间比较；非 ASCII token 也安全（`compare_digest` 对 str 仅支持 ASCII）。"""
    if not candidate:
        return False
    return secrets.compare_digest(candidate.encode("utf-8"), WORKER_TOKEN.encode("utf-8"))


def _verify_token(token: str | None, authorization: str | None = None) -> None:
    """WORKER_TOKEN 未配置时不校验；配置后要求完全一致。

    接受两种携带方式：
    - `X-Worker-Token: <token>`（P2 约定，api 侧 FcDispatcher 默认发这个）
    - `Authorization: Bearer <token>`（FC 触发器若改成 Bearer 认证，网关会先校验，
      到达容器时仍是这个头；两种都认可以避免切换认证方式时改代码）
    """
    if not WORKER_TOKEN:
        return
    if _token_matches(token):
        return
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and _token_matches(value.strip()):
            return
    raise HTTPException(status_code=401, detail="worker token 无效")


def _decode_body(value: Any, is_base64: bool) -> Any:
    if is_base64 and isinstance(value, str):
        try:
            value = base64.b64decode(value)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("body 不是合法 base64") from exc
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode()
        except UnicodeDecodeError as exc:
            raise ValueError("body 不是合法 UTF-8") from exc
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("body 不是合法 JSON") from exc
    return value


def _unwrap_event(payload: Any) -> dict:
    """把各种平台包装体解成裸作业体（含 task_id 的 dict）。

    只认「有 task_id 就是作业体」这一条；否则按 body / payload / event / data
    逐层下钻。FC 自定义容器异步调用的事件投递格式未经官方文档核实，故宽容兼容。
    """
    current = payload
    for _ in range(_MAX_UNWRAP_DEPTH):
        if isinstance(current, (str, bytes, bytearray)):
            current = _decode_body(current, is_base64=False)
            continue
        if not isinstance(current, dict):
            break
        if "task_id" in current:
            return current
        is_base64 = bool(current.get("isBase64Encoded"))
        for key in ("body", "payload", "event", "data"):
            if key in current:
                current = _decode_body(current[key], is_base64 and key == "body")
                break
        else:
            break
    if not isinstance(current, dict):
        raise ValueError("事件体不是 JSON 对象")
    return current


def _parse_job(payload: Any) -> TranscribeJob:
    try:
        body = _unwrap_event(payload)
        return TranscribeJob.from_dict(body)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"作业体无法解析：{exc}") from exc


def _looks_like_timer_event(payload: Any) -> bool:
    """判定是否 FC 定时触发器事件：`{"triggerTime": ..., "triggerName": ..., "payload": ...}`。

    沿与 `_unwrap_event` 相同的包装层下钻，兼容平台将来把 event 塞进 body 的情形。
    只要求两个特征字段**同时存在**，不解析 payload 内容——payload 是用户自定义字符串，
    依赖它做路由会出现「忘配 payload → reaper 静默不跑」这类更难排查的故障。
    """
    current = payload
    for _ in range(_MAX_UNWRAP_DEPTH):
        if isinstance(current, (str, bytes, bytearray)):
            try:
                current = _decode_body(current, is_base64=False)
            except ValueError:
                return False
            continue
        if not isinstance(current, dict):
            return False
        if "triggerTime" in current and "triggerName" in current:
            return True
        is_base64 = bool(current.get("isBase64Encoded"))
        for key in ("body", "payload", "event", "data"):
            if key in current:
                value = current[key]
                if isinstance(value, (str, bytes, bytearray)):
                    try:
                        value = _decode_body(value, is_base64 and key == "body")
                    except ValueError:
                        return False
                current = value
                break
        else:
            return False
    return False


def _lock_renewer(task_id: str, token: str, loop: asyncio.AbstractEventLoop) -> Callable[[], None]:
    """返回同步续期回调：pipeline 心跳线程 → 投递到 Worker 事件循环执行。

    pipeline 跑在 to_thread 的工作线程里（无 running loop），所以不能用桥接；
    直接 run_coroutine_threadsafe 到 Worker 自己的 loop。
    """
    store = task_store.get_task_store()

    def _renew() -> None:
        future = asyncio.run_coroutine_threadsafe(
            store.renew(task_id, token, TASK_LOCK_SECONDS), loop
        )
        if not future.result(timeout=10):
            logger.warning("任务锁续期失败（task_id=%s）：锁可能已被他人持有", task_id)

    return _renew


async def _mark_failed_if_processing(task_id: str) -> None:
    """胶水层兜底：pipeline 内部的异常已自行收敛，这里只处理逃出来的基础设施故障。"""
    store = task_store.get_task_store()
    try:
        current = await store.get(task_id)
        if current is not None and current.status == "processing":
            await store.update(
                task_id,
                status="failed",
                error={"code": "TRANSCRIBE_FAILED", "message": "任务执行失败，请重试"},
            )
    except Exception:  # pragma: no cover - 存储也挂了，只能靠 reaper
        logger.exception("兜底标记失败也失败（task_id=%s）", task_id)


async def _run_job(job: TranscribeJob) -> dict:
    store = task_store.get_task_store()

    task = await store.get(job.task_id)
    if task is None:
        logger.error("Worker 收到未知任务（task_id=%s）：可能已过 TTL", job.task_id)
        return {"status": "skipped", "reason": _SKIP_NOT_FOUND, "task_id": job.task_id}
    if task.status != "processing":
        # 已完成/已失败的确定性结果：重跑只会重复计费
        return {"status": "skipped", "reason": f"already_{task.status}", "task_id": job.task_id}

    token = await store.acquire(job.task_id, TASK_LOCK_SECONDS)
    if token is None:
        logger.info("任务已被其他实例领取（task_id=%s），跳过", job.task_id)
        return {"status": "skipped", "reason": "locked", "task_id": job.task_id}

    try:
        # 抢到锁后复查：两次检查之间可能已被别的实例跑完
        task = await store.get(job.task_id)
        if task is None or task.status != "processing":
            status = task.status if task is not None else "missing"
            return {"status": "skipped", "reason": f"already_{status}", "task_id": job.task_id}

        await store.update(job.task_id, attempts=(task.attempts or 0) + 1)

        loop = asyncio.get_running_loop()
        # pipeline 是同步实现（yt-dlp / ffmpeg / ASR 全阻塞），必须让出事件循环；
        # 且其内部的 update_task 走同步桥接，在事件循环内直接调用会抛 RuntimeError。
        await asyncio.to_thread(
            transcribe.run_transcription_pipeline,
            job.task_id,
            job.url,
            job.video,
            job.prefer_subtitles,
            job.hotwords,
            job.engine,
            on_heartbeat=_lock_renewer(job.task_id, token, loop),
        )
    except Exception as exc:
        logger.exception("Worker 执行失败（task_id=%s）", job.task_id)
        await _mark_failed_if_processing(job.task_id)
        raise HTTPException(status_code=500, detail="worker execution failed") from exc
    finally:
        await store.release(job.task_id, token)

    final = await store.get(job.task_id)
    return {
        "status": "ok",
        "task_id": job.task_id,
        "task_status": final.status if final is not None else None,
    }


@app.post(WORKER_PATH)
async def run_transcribe_job(
    payload: Any = Body(...),
    x_worker_token: str | None = Header(default=None, alias="X-Worker-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    """执行一次转写作业（HTTP 触发器异步调用入口，主路径）。"""
    await _ensure_initialized()
    _verify_token(x_worker_token, authorization)
    return await _run_job(_parse_job(payload))


@app.post(INVOKE_PATH)
async def run_invoke_entry(
    request: Request,
    x_worker_token: str | None = Header(default=None, alias="X-Worker-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    """`InvokeFunction` API 与事件源触发器（含定时触发器）的统一入口。

    FC 固定把 event 作为 body POST 到 `/invoke`（octet-stream），因此这里直接读原始
    字节，不依赖 FastAPI 的 JSON 解析。分流顺序见模块 docstring：Timer 优先，
    其次是转写作业。
    """
    await _ensure_initialized()
    _verify_token(x_worker_token, authorization)

    raw = await request.body()
    try:
        payload = _decode_body(raw, is_base64=False) if raw else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"事件体无法解析：{exc}") from exc

    if _looks_like_timer_event(payload):
        logger.info("收到定时触发器事件，执行僵尸任务清理")
        return (await reaper.reap_once()).to_dict()

    return await _run_job(_parse_job(payload))


@app.post(REAP_PATH)
async def reap_stale_tasks(
    x_worker_token: str | None = Header(default=None, alias="X-Worker-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    """僵尸任务清理（S2-P4）：本地脚本 / 人工 curl 入口。

    线上由 FC 定时触发器经 `/invoke` 触发（定时触发器无法指定 HTTP 路径），
    这个端点存在的意义是「不依赖云上定时器也能验一遍 reaper」。
    """
    await _ensure_initialized()
    _verify_token(x_worker_token, authorization)
    return (await reaper.reap_once()).to_dict()


@app.get("/api/health")
async def health() -> dict:
    """存活探针（FC 自定义容器 / 本地调试用）。"""
    return {"status": "ok", "role": "worker"}
