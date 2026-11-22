"""重点帧抽帧管线（PR #89，URL 任务）：B站视频流 → 逐时间点 ffmpeg 抽帧 → OSS。

执行环境：Worker（FC 自定义容器，镜像内静态 ffmpeg 已就位，与音频转码共用）。
抽帧是任务 completed 之后的**二次作业**（前端在结果页手动触发）：不碰任务的
status/stage/progress（终态保护语义见 tasks.py），只写 task.frames 字段。

流程：
1. 从任务的视频页 URL 提取 BV 号（与 audio._download_via_playurl 同源的平台判定）
2. playurl API（try_look=1，见 bili_playurl.get_video_stream）拿最高 1080P 的
   DASH 视频流，优先 HEVC（码率低，下载代价最小）
3. httpx 流式下载到系统临时目录（单文件，受 FRAMES_VIDEO_MAX_MB 约束，超限即失败）
4. 逐时间点 `ffmpeg -ss <t> -i <文件> -frames:v 1` 抽帧——输入寻址（-ss 在 -i 前）
   直接跳到目标关键帧，1080P HEVC 实测单帧 ~0.3s；缩放限宽 1280（不放大窄视频），
   JPEG 质量 q:v 3，单张 30-50KB
5. JPEG 逐张经 storage.put_object_bytes 传 OSS frames/ 前缀（对象生命周期 7 天，
   与任务 TTL 对齐，见 setup_oss.py）

失败语义：
- 单帧失败（超时/解码错误）→ 跳过该时间点继续，不致命
- 整段失败（下载失败 / 一帧都没抽出）→ task.frames.status = failed，错误信息
  收敛进 frames.error（异步作业的失败不是 HTTP 错误——用户在结果页轮询任务
  详情看到结果）
"""

import logging
import os
import re
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

import httpx

from app.config import FRAMES_MAX_COUNT, FRAMES_PRESIGN_EXPIRES_SECONDS, FRAMES_VIDEO_MAX_HEIGHT, FRAMES_VIDEO_MAX_MB
from app.errors import AppException, FramesUnsupportedError
from app.schemas import AppErrorBody, FramesItem, TaskFramesOut
from app.services import bili_playurl
from app.services import storage
from app.services.tasks import update_task

logger = logging.getLogger(__name__)

# 抽帧点最小间隔（秒）：更近的两个点抽出来的画面几乎一样，合并掉。
# 与前端派生逻辑（frames-card.tsx）保持同一口径。
FRAME_MIN_GAP_SECONDS = 2.0

# 帧图最大宽度（像素）：1280 宽在结果页网格里足够清晰，且单张体积可控。
# 窄于 1280 的源视频不放大（scale 表达式取 min）。
_FRAME_MAX_WIDTH = 1280

# 单帧抽取上限（秒）：1080P HEVC 实测单帧 ~0.3s，留足余量防个别坏帧卡死
_FFMPEG_FRAME_TIMEOUT = 60

# 视频流下载超时（秒）：1080P HEVC 27 分钟约 150MB，FC 到 bilivideo CDN
# 通常数秒；给足慢网余量但不挂死
_DOWNLOAD_TIMEOUT = 300.0

_DOWNLOAD_CHUNK_SIZE = 64 * 1024

# 与 audio._looks_like_error_page 同口径：CDN 缺请求头时回 403 + text/html
_ERROR_CONTENT_TYPES = ("text/", "application/json", "application/xml")


def sanitize_timestamps(
    timestamps: list[float], duration: float | int | None, max_count: int = FRAMES_MAX_COUNT
) -> list[float]:
    """钳制/去重/截断抽帧时间点（路由与作业体两侧共用同一规则）。

    - 非法值（NaN/inf/非数字）丢弃；负数钳到 0
    - duration > 1 时钳到 duration - 1（末尾 1s 的帧可能解码失败/黑屏）
    - 升序排序后按 FRAME_MIN_GAP_SECONDS（2s）合并近邻
    - 截断到 max_count
    """
    duration_value = float(duration or 0)
    cleaned: list[float] = []
    for value in timestamps or []:
        try:
            point = float(value)
        except (TypeError, ValueError):
            continue
        if point != point or point in (float("inf"), float("-inf")):  # NaN / ±inf
            continue
        point = max(0.0, point)
        if duration_value > 1:
            point = min(point, duration_value - 1.0)
        cleaned.append(round(point, 2))
    cleaned.sort()
    deduped: list[float] = []
    for point in cleaned:
        if deduped and point - deduped[-1] < FRAME_MIN_GAP_SECONDS:
            continue
        deduped.append(point)
    return deduped[: max(1, int(max_count))]


def run_frames_pipeline(task_id: str, url: str, timestamps: list[float]) -> None:
    """抽帧作业主体（Worker / 本地 inline 共用）。任何异常收敛为 task.frames=failed。

    与 run_transcription_pipeline 的差异：调用方（Worker）已确认任务存在且
    frames 未在处理中；这里不再查锁、不再写进度，结束（或失败）时一次写入
    frames 终态。时间戳在本侧重新净化一遍（防御纵深：派发载荷虽需 WORKER_TOKEN，
    也不信任其原始值）。
    """
    safe_points = sanitize_timestamps(timestamps, duration=None)
    try:
        items = _extract_all(task_id, url, safe_points)
        if not items:
            raise AppException("FRAMES_FAILED", "未能抽出任何一帧，请换一个时间点或稍后重试", 500)
        update_task(task_id, frames={"status": "ready", "error": None, "items": items})
        logger.info("重点帧完成（task_id=%s）：%d 帧", task_id, len(items))
    except Exception as exc:
        message = (
            exc.message
            if isinstance(exc, AppException)
            else _clean_message(exc)
        )
        logger.warning("重点帧失败（task_id=%s）：%s", task_id, message)
        update_task(
            task_id,
            frames={
                "status": "failed",
                "error": {"code": "FRAMES_FAILED", "message": message},
                "items": [],
            },
        )


def _clean_message(exc: Exception) -> str:
    """stderr / 网络异常 → 可展示的单行文案（去 ANSI、截断、防空）。"""
    from app.errors import strip_ansi

    text = strip_ansi(str(exc) or "").strip().replace("\n", " ")[:200]
    return text or "重点帧生成失败，请稍后重试"


def frames_to_out(frames_state: dict | None, *, presign: bool = False) -> TaskFramesOut:
    """任务库的 frames dict → 契约模型（TaskFramesOut）。

    presign=True 时逐帧现场签发预签名 GET URL——预签名限时，任务里只存
    objectKey，每次读取（任务详情 / 幂等重发响应）现场签，前端重拉即续期。
    单帧签名失败只跳过该帧的 URL（前端按 url 为空兜底），不影响其余帧。
    """
    state = frames_state or {}
    status = str(state.get("status") or "")
    items: list[FramesItem] = []
    for item in state.get("items") or []:
        object_key = str(item.get("objectKey") or "")
        if not object_key:
            continue
        url = ""
        if presign:
            try:
                url = storage.presign_get(object_key, FRAMES_PRESIGN_EXPIRES_SECONDS)
            except Exception:  # pragma: no cover —— 单帧签名失败不影响其余帧
                logger.warning("重点帧预签名失败（key=%s）", object_key, exc_info=True)
        items.append(
            FramesItem(time=float(item.get("time") or 0.0), objectKey=object_key, url=url)
        )
    error = state.get("error")
    return TaskFramesOut(
        status=status if status in ("processing", "ready", "failed") else "failed",
        error=(
            AppErrorBody(
                code=str(error.get("code") or "FRAMES_FAILED"),
                message=str(error.get("message") or ""),
            )
            if isinstance(error, dict) and error
            else None
        ),
        items=items,
    )


def _extract_all(task_id: str, url: str, timestamps: list[float]) -> list[dict]:
    """取流 → 下载 → 逐点抽帧 → 传 OSS。返回可用的 frames items。

    下载与帧文件全部落在 tempfile 管理的临时目录（路径由系统生成，不经任何
    用户输入），用完即删——与 storage.download_to_temp 同一安全口径。
    """
    bvid = _resolve_bvid(url)
    stream = bili_playurl.get_video_stream(bvid, max_height=FRAMES_VIDEO_MAX_HEIGHT)
    logger.info(
        "抽帧取流（task_id=%s）：qn=%d %dx%d %s bw=%d 备用=%d",
        task_id, stream.quality, stream.width, stream.height,
        stream.codecs, stream.bandwidth, len(stream.backup_urls),
    )

    items: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="vtt-frames-") as tmp:
        source_path = _download_video_stream(stream, FRAMES_VIDEO_MAX_MB * 1024 * 1024, tmp)
        for index, point in enumerate(timestamps):
            frame_path = os.path.join(tmp, f"frame_{index:02d}.jpg")
            try:
                _extract_frame(source_path, point, frame_path)
            except Exception as exc:  # noqa: BLE001 —— 单帧失败不致命，跳过继续
                logger.warning("抽帧点 %.2fs 失败，跳过（task_id=%s）：%s", point, task_id, exc)
                continue
            data = Path(frame_path).read_bytes()
            object_key = f"frames/{task_id}/{index:02d}.jpg"
            storage.put_object_bytes(object_key, data, "image/jpeg")
            items.append({"time": point, "objectKey": object_key})
    return items


def _resolve_bvid(url: str) -> str:
    """从任务的视频页 URL 提取 BV 号；非 B站链接一律 FRAMES_UNSUPPORTED。

    与 audio._download_via_playurl 同口径的平台判定（detect_platform），保证
    「转写认定的 B站任务」与「抽帧认定的 B站任务」是同一套规则。
    """
    from app.platforms import detect_platform

    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    meta = detect_platform(host)
    if meta is None or meta.id != "bilibili":
        raise FramesUnsupportedError()
    match = re.search(r"/video/(BV[\w]+)", parsed.path, re.IGNORECASE)
    if not match:
        raise FramesUnsupportedError("未能从链接中识别 B站视频号（BV）")
    return match.group(1)


def _download_video_stream(
    stream: bili_playurl.VideoStream, limit_bytes: int, tmp_dir: str
) -> str:
    """流式下载视频流到 tmp_dir（系统生成的临时文件名），返回落盘路径。

    超限/错误页即换备用地址重试，全部失败抛 ValueError。CDN 请求头要求与
    音频下载一致（bili_playurl._headers：UA + Referer + Cookie）。
    写盘走 NamedTemporaryFile：文件名完全由系统生成，不存在用户可控路径。
    """
    headers = bili_playurl._headers()
    candidates = (stream.url,) + stream.backup_urls
    last_error: Exception | None = None
    for index, candidate in enumerate(candidates):
        handle = tempfile.NamedTemporaryFile(
            dir=tmp_dir, prefix="source_", suffix=".m4s", delete=False
        )
        try:
            total = 0
            with handle:
                with httpx.stream(
                    "GET", candidate, headers=headers, timeout=_DOWNLOAD_TIMEOUT,
                    follow_redirects=True,
                ) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    if content_type.startswith(_ERROR_CONTENT_TYPES):
                        raise ValueError(f"视频直链返回错误页（content-type={content_type}）")
                    length = response.headers.get("content-length")
                    if length and length.isdigit() and int(length) > limit_bytes:
                        raise ValueError(_oversize_message(limit_bytes))
                    for chunk in response.iter_bytes(_DOWNLOAD_CHUNK_SIZE):
                        total += len(chunk)
                        if total > limit_bytes:
                            raise ValueError(_oversize_message(limit_bytes))
                        handle.write(chunk)
            if total == 0:
                raise ValueError("视频流下载为空")
            return handle.name
        except Exception as exc:  # noqa: BLE001 —— 换下一个地址，最后统一上报
            last_error = exc
            logger.warning("视频地址下载失败（第 %d 个）：%s", index + 1, exc)
            _safe_remove(handle.name)
    raise ValueError(f"所有视频地址均下载失败：{last_error}")


def _oversize_message(limit_bytes: int) -> str:
    return f"视频流超过 {limit_bytes // (1024 * 1024)}MB 上限，无法抽帧"


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _extract_frame(source_path: str, timestamp: float, target_path: str) -> None:
    """单帧抽取：输入寻址跳点 → 缩放限宽 → JPEG 落盘 target_path。

    ffmpeg 调用纪律与 audio.convert_* 一致：Popen + communicate(timeout)，
    stderr 由 communicate 消费，超时 kill 后必须再 communicate 回收。
    输出文件由 ffmpeg 自行创建（本模块不做任何用户可控路径的写文件操作）。
    """
    proc = subprocess.Popen(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{timestamp:.2f}",
            "-i", source_path,
            "-frames:v", "1",
            "-vf", f"scale='min({_FRAME_MAX_WIDTH},iw)':-2",
            "-q:v", "3",
            "-f", "image2",
            target_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _stdout, stderr = proc.communicate(timeout=_FFMPEG_FRAME_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.communicate()
        raise ValueError(f"第 {timestamp:.0f} 秒抽帧超时") from exc
    if proc.returncode != 0:
        detail = _clean_message(ValueError(stderr.decode("utf-8", errors="replace")))
        raise ValueError(detail or f"第 {timestamp:.0f} 秒抽帧失败")
    if not os.path.exists(target_path) or os.path.getsize(target_path) == 0:
        raise ValueError(f"第 {timestamp:.0f} 秒抽帧产物为空")
