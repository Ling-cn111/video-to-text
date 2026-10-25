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

鉴权（P0 封堵，fail-closed）：
`WORKER_TOKEN` 未配置时**拒绝一切请求**（503），不再「未配置就放行」——线上漏配
一个环境变量不该等于把异步执行入口敞开给公网。所有入口（含 `/invoke`）一律
先验 token，没有任何免鉴权分支。

S2-P4 定时触发器分流（关键约束）：
阿里云 FC 的定时触发器是**事件调用**，不能指定 HTTP 路径——FC 固定投递到 `/invoke`。
因此 reaper 不能只挂在 `/internal/worker/reap` 上，否则线上永远不会被触发。
`/invoke` 验完 token 后按事件特征分流，顺序固定为：
1. 含 `triggerTime` + `triggerName` → 定时事件，跑 reaper
2. 含 `task_id` → 转写作业
3. 其它 → 422

定时触发器不能自定义请求头，带不了 `X-Worker-Token`，所以**凭据放在触发器
payload 里**（`{"action": "reap", "token": "<WORKER_TOKEN>"}`，见 s.yaml 的
`reapTimer`），由 `_timer_payload_token` 从事件体取出后交给同一套校验。
不能靠「事件体里出现 triggerTime/triggerName 就信任」——`/invoke` 是公网
匿名 HTTP 入口，任何人都能伪造这两个字段，那正是被审计打回的漏洞。

`/internal/worker/reap` 保留给本地脚本与人工 curl（Timer 打不到它）。

执行语义：
1. 任务不存在 / 非 processing → 200 skipped（确定性结果，不触发平台重试）
2. `SET NX EX` 抢锁失败 → 200 skipped（另一实例在处理）
3. pipeline 业务异常 → pipeline 内部已收敛为 failed，返回 200（重试无意义且会重复计费）
4. 胶水层/未捕获异常 → 标 failed 后返回 5xx（基础设施类问题，值得平台重试）
5. 定时触发器事件 → 跑 reaper，返回收割计数（与作业路径完全隔离）
6. `job.url` 未通过 `validate_public_http_url`（内网/非法地址）→ 标 failed + 200 skipped
   （P0 SSRF 兜底：不信任派发方，Worker 侧独立复核一次）
"""
import asyncio
import base64
import binascii
import json
import logging
import secrets
import time
from typing import Any, Callable

import httpx
from fastapi import Body, FastAPI, Header, HTTPException, Request

from app.config import TASK_LOCK_SECONDS, WORKER_TOKEN
from app.errors import InvalidUrlError
from app.services import reaper
from app.services import tasks as task_store
from app.services import transcribe
from app.services.dispatcher import TranscribeJob
from app.url_guard import validate_public_http_url

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

# URL 未通过公网校验时的 skipped 原因（P0 SSRF 兜底）
_SKIP_INVALID_URL = "invalid_url"

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


def _verify_token(
    token: str | None,
    authorization: str | None = None,
    *,
    payload_token: str | None = None,
) -> None:
    """fail-closed 鉴权：`WORKER_TOKEN` 未配置时拒绝一切请求（503）。

    旧实现是「未配置就放行」，等于把异步执行入口敞开给公网——线上漏配一个环境
    变量就能被人拿去跑任意 URL（SSRF）与刷额度。宁可整条链路不可用，也不放行。

    接受三种携带方式（前两种来自请求头，第三种来自定时触发器 payload），任意一种
    匹配即通过：
    - `X-Worker-Token: <token>`（P2 约定，api 侧 FcDispatcher 默认发这个）
    - `Authorization: Bearer <token>`（FC 触发器若改成 Bearer 认证，网关会先校验，
      到达容器时仍是这个头；两种都认可以避免切换认证方式时改代码）
    - 事件体 payload 里的 `token` 字段（定时触发器带不了自定义头，见
      `_timer_payload_token`）

    三种是「或」关系而不是「头优先」：触发器可能带上网关注入的占位头，若头存在就
    短路，payload 凭据会被无声丢弃。
    """
    if not WORKER_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="worker token 未配置，拒绝执行（请设置 WORKER_TOKEN 环境变量）",
        )
    if _token_matches(token):
        return
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and _token_matches(value.strip()):
            return
    if payload_token and _token_matches(payload_token):
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


def _timer_payload_token(payload: Any) -> str | None:
    """从定时触发器事件体的 `payload` 里取共享密钥（P0-2 的补偿机制）。

    FC 的定时触发器不能自定义请求头，带不了 `X-Worker-Token`，所以凭据放在
    触发器配置的 payload 里（s.yaml → `reapTimer.payload`）。取不到就返回 None，
    由 `_verify_token` 走正常 401 分支——**不存在「是定时事件就放行」的分支**。

    payload 可能是 JSON 字符串（s.yaml 里就是字符串形态），也可能是对象；
    只认顶层 `token` 字段，不做模糊匹配。
    """
    current = payload
    for _ in range(_MAX_UNWRAP_DEPTH):
        if isinstance(current, (str, bytes, bytearray)):
            try:
                current = _decode_body(current, is_base64=False)
            except ValueError:
                return None
            continue
        if not isinstance(current, dict):
            return None
        if "triggerTime" in current and "triggerName" in current:
            inner = current.get("payload")
            if isinstance(inner, (str, bytes, bytearray)):
                try:
                    inner = _decode_body(inner, is_base64=False)
                except ValueError:
                    return None
            if not isinstance(inner, dict):
                return None
            token = inner.get("token")
            return token if isinstance(token, str) and token else None
        is_base64 = bool(current.get("isBase64Encoded"))
        for key in ("body", "payload", "event", "data"):
            if key in current:
                value = current[key]
                if isinstance(value, (str, bytes, bytearray)):
                    try:
                        value = _decode_body(value, is_base64 and key == "body")
                    except ValueError:
                        return None
                current = value
                break
        else:
            return None
    return None


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

    # P0 SSRF 兜底：不信任派发方（API 侧已校验过一次），Worker 侧独立复核。
    # 不能直接抛 InvalidUrlError —— 那会变成 5xx，触发 FC 异步重试无限循环；
    # 这里按「确定性失败」处理：标 failed + 200 skipped。
    try:
        validate_public_http_url(job.url)
    except InvalidUrlError as exc:
        logger.warning("Worker 拒绝非公网 URL（task_id=%s）：%s", job.task_id, exc.message)
        current = await store.get(job.task_id)
        if current is not None and current.status == "processing":
            await store.update(
                job.task_id,
                status="failed",
                error={"code": "INVALID_URL", "message": exc.message},
            )
        return {"status": "skipped", "reason": _SKIP_INVALID_URL, "task_id": job.task_id}

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

        await store.update(
            job.task_id,
            attempts=(task.attempts or 0) + 1,
            started_at=time.time(),
        )

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
    字节，不依赖 FastAPI 的 JSON 解析。

    **鉴权先于一切分流**（P0-2）：旧实现「先判 timer 特征、是 timer 就跳过鉴权」，
    而 `/invoke` 是 `authType: anonymous` 的公网 HTTP 入口——任何人 POST 一个带
    `triggerTime`/`triggerName` 的 body 就能触发 reaper（伪造定时任务）。
    现在所有请求统一先过 `_verify_token`，定时触发器的凭据来自它自己的 payload
    （`_timer_payload_token`），不存在免鉴权分支。
    """
    await _ensure_initialized()

    raw = await request.body()
    try:
        payload = _decode_body(raw, is_base64=False) if raw else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"事件体无法解析：{exc}") from exc

    # 凭据来源：请求头 / 事件体 payload（定时触发器），三者「或」关系
    _verify_token(x_worker_token, authorization, payload_token=_timer_payload_token(payload))

    if _looks_like_timer_event(payload):
        # 走到这里说明 token 已通过校验；reap_once 自身还有互斥锁 + CAS 幂等
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


DIAG_PATH = "/internal/worker/diagnose"


@app.post(DIAG_PATH)
async def diagnose_upstream(
    bvid: str = "BV1GJ411x7h7",
    x_worker_token: str | None = Header(default=None, alias="X-Worker-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    """出口连通性诊断（运维用，需 WORKER_TOKEN）。

    为什么需要它：B站风控（HTTP 412）只在**云端机房出口**出现，本地家庭宽带
    复现不了。改配置后如果只能靠「发一个转写任务等 10 分钟看失败」来验证，
    排查成本极高。这里一次性测完关键上游，返回每个目标的真实状态码。

    设计成**对照组**：同一批 URL 分别用「裸请求头」和「生产实际用的请求头」
    各测一遍。这样一次调用就能回答「加了指纹/UA/Cookie 到底有没有用」，
    不必改一次配置部署一次。云端实测结论：裸请求三路全 412，
    带设备指纹后转写链路恢复。

    只做只读探测、不写入任何数据；不返回 Cookie 值，只说明是否已配置。
    """
    _verify_token(x_worker_token, authorization)

    import os

    from app import config
    from app.services import bili_player_api, ytdlp_options

    probe_ua = config.BILI_USER_AGENT or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    baseline_headers = {"User-Agent": probe_ua, "Referer": "https://www.bilibili.com/"}
    hardened_headers = {**baseline_headers, **ytdlp_options.base_options()["http_headers"]}

    target_bvid = bvid
    targets = {
        "view_api": f"https://api.bilibili.com/x/web-interface/view?bvid={target_bvid}",
        "video_page": f"https://www.bilibili.com/video/{target_bvid}",
        "finger_spi": "https://api.bilibili.com/x/frontend/finger/spi",
    }

    def probe(headers: dict[str, str]) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for name, url in targets.items():
            try:
                response = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
                entry: dict[str, Any] = {"status": response.status_code}
                if name.endswith("_api") and response.status_code == 200:
                    try:
                        entry["code"] = response.json().get("code")
                    except ValueError:
                        entry["code"] = None
                results[name] = entry
            except Exception as exc:  # 网络异常也要如实报告
                results[name] = {"error": f"{type(exc).__name__}: {exc}"}
        return results

    cookie = ytdlp_options.cookie_header()
    return {
        "bvid": target_bvid,
        "egress": {
            "bili_cookie_configured": bool(config.BILI_COOKIE),
            "bili_user_agent_configured": bool(config.BILI_USER_AGENT),
            # 只报「有没有指纹」，不回显指纹值本身（避免日志泄露可用于冒用的标识）
            "device_fingerprint_present": "buvid3=" in cookie,
            "container_index": os.getenv("FC_INSTANCE_ID", ""),
        },
        # 同一出口 IP、同一批 URL，只有请求头不同 → 差异即结论
        "baseline": probe(baseline_headers),
        "hardened": probe(hardened_headers),
        # 网页路径专项：yt-dlp 抓的是 html，比 API 更容易被拦
        "video_page_variants": _probe_video_page_variants(bvid, probe_ua, cookie),
        # 音频路径专项：生产实际依赖 playurl API 绕开网页，这里直接验一次
        "audio_path": _probe_audio_path(bvid),
        # 顺带看一眼 dm/view 层级是否还能用（字幕快路径依赖它）
        "subtitle_tracks": len(bili_player_api.get_subtitle_tracks(bvid)),
    }


def _probe_audio_path(bvid: str) -> dict[str, Any]:
    """验证 playurl API 这条路在生产环境真的能取到音频流。

    `download_audio` 现在的顺序是 playurl → yt-dlp。如果 playurl 在云端失败，
    就会静默回退到 yt-dlp，而 yt-dlp 因网页 412 必然失败——最终用户只看到
    412，完全不知道真正的失败点在 playurl。这个探针把中间步骤暴露出来。
    """
    from app.services import bili_playurl

    try:
        stream = bili_playurl.get_audio_stream(bvid)
        result: dict[str, Any] = {
            "ok": True,
            "bandwidth": stream.bandwidth,
            "mime_type": stream.mime_type,
            "backup_urls": len(stream.backup_urls),
        }
        # 真去碰一下 CDN，确认请求头组合在云端也成立（缺一个就 403）
        try:
            with httpx.stream(
                "GET", stream.url, headers=bili_playurl._headers(), timeout=20
            ) as response:
                result["cdn_status"] = response.status_code
                result["cdn_content_type"] = response.headers.get("content-type")
        except Exception as exc:
            result["cdn_error"] = f"{type(exc).__name__}: {exc}"
        return result
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _probe_video_page_variants(bvid: str, ua: str, cookie: str) -> dict[str, Any]:
    """对比「只有指纹」与「先访问首页拿全 Cookie 会话」两条网页路径。

    背景：云端实测 hardened 的 view_api 已 200，但 video_page 仍 412。
    yt-dlp 抓的正是视频页 html，所以没有 CC 字幕、必须下载音频的视频仍会失败。

    B站对 html 的校验比 JSON API 严，除了 buvid3 还看 `b_nut`（首页下发的
    首次访问时间戳）。这里用真实 Client 会话（自动收 Set-Cookie）验证：
    先 GET 首页、再 GET 视频页，能否让 412 变成 200。
    """
    url = f"https://www.bilibili.com/video/{bvid}"
    results: dict[str, Any] = {}

    # 变体 1：只有指纹（= 当前生产行为）
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            response = client.get(
                url,
                headers={
                    "User-Agent": ua,
                    "Referer": "https://www.bilibili.com/",
                    **({"Cookie": cookie} if cookie else {}),
                },
            )
            results["cookie_only"] = {
                "status": response.status_code,
                "bytes": len(response.content),
            }
    except Exception as exc:
        results["cookie_only"] = {"error": f"{type(exc).__name__}: {exc}"}

    # 变体 2：先访问首页建立会话，再抓视频页
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            client.headers.update({"User-Agent": ua})
            home = client.get("https://www.bilibili.com/")
            page = client.get(url, headers={"Referer": "https://www.bilibili.com/"})
            results["warm_session"] = {
                "status": page.status_code,
                "bytes": len(page.content),
                "home_status": home.status_code,
                "cookie_names": sorted(client.cookies.keys()),
            }
    except Exception as exc:
        results["warm_session"] = {"error": f"{type(exc).__name__}: {exc}"}

    return results


@app.get("/api/health")
async def health() -> dict:
    """存活探针（FC 自定义容器 / 本地调试用）。"""
    return {"status": "ok", "role": "worker"}
