"""转写流水线编排：字幕快路径 → 音频下载 → ASR，全程更新任务进度。

入口 run_transcription_pipeline 交由 FastAPI BackgroundTasks 在响应返回后执行。
音频全程内存化（S1-B）：下载/转换/分离均以 BytesIO 流转，不落持久磁盘；
仅 demucs / qwen3 / funasr 等「只认文件路径」的组件内部用系统临时目录并即用即删。

M3 本地文件任务（source=file）：没有链接语义，跳过字幕快路径与 download_audio，
改为「OSS 对象 → /tmp（流式落盘）→ ffmpeg 按路径转码」；转码后与 URL 路径在
separation/ASR 汇合。成功后删除 OSS 对象，失败保留 ≤1 天供重试。

S2-P0 进度上报（ProgressReporter）：
- 节流：进度变化 < PROGRESS_STEP 且阶段未变时不写存储（云端 ASR 无回调，
  本地 ASR 每分段都回调，节流后写入次数从「分段数」降到「进度档数」）
- 心跳：独立守护线程每 TASK_HEARTBEAT_SECONDS 刷新一次 updated_at，
  覆盖下载/FFmpeg/ASR 等长阻塞步骤——没有心跳，reaper（S2-P4）无法区分
  「实例被回收的僵尸任务」与「正在跑的任务」，Serverless 下会误杀。
"""
import logging
import threading
import time
from typing import Callable

from app.config import FILE_MAX_SIZE_MB, TASK_HEARTBEAT_SECONDS
from app.errors import AppException
from app.schemas import FileRef, TranscriptItem, VideoInfo
from app.services import asr
from app.services import storage
from app.services.audio import convert_file_to_wav_16k_mono, convert_to_wav_16k_mono, download_audio
from app.services.subtitles import punctuate_subtitle_items, try_extract_subtitle_transcript
from app.services.tasks import update_task

logger = logging.getLogger(__name__)

# 各阶段进度锚点
PROGRESS_PARSE = 5
PROGRESS_EXTRACT_START = 15
PROGRESS_EXTRACT_DONE = 35
PROGRESS_ASR_START = 40
PROGRESS_ASR_SPAN = 55

# 进度写入最小步长：小于该值的进度变化不落存储（阶段切换始终写）
PROGRESS_STEP = 5


class ProgressReporter:
    """任务进度上报器：节流写入 + 定时心跳（S2-P0）。

    - `set(stage, progress)`：阶段变化或进度涨幅 ≥ PROGRESS_STEP 才写
    - 心跳线程：距上次写入超过 heartbeat_seconds 时补一次写入（只刷 updated_at 语义，
      进度值保持不变），保证长阻塞步骤期间任务不被判为「陈旧」
    - `close()`：停止心跳；之后的 `set()` 变成 no-op（避免收尾后又被心跳覆盖）
    """

    def __init__(
        self,
        task_id: str,
        heartbeat_seconds: float | None = None,
        on_heartbeat: "Callable[[], None] | None" = None,
    ) -> None:
        self._task_id = task_id
        self._lock = threading.Lock()
        self._last_progress = -1
        self._last_stage: str | None = None
        self._last_write = 0.0
        self._closed = False
        self._interval = TASK_HEARTBEAT_SECONDS if heartbeat_seconds is None else float(heartbeat_seconds)
        self._on_heartbeat = on_heartbeat
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if self._interval <= 0 and on_heartbeat is not None:
            # 心跳关闭但需要续期（Worker 锁）：按默认 30s 兜底，避免锁静默过期
            logger.warning("TASK_HEARTBEAT_SECONDS<=0 但传入了 on_heartbeat，心跳按 30s 兜底")
            self._interval = 30.0
        if self._interval > 0:
            self._thread = threading.Thread(
                target=self._heartbeat, name=f"task-heartbeat-{task_id[:8]}", daemon=True
            )
            self._thread.start()

    def set(self, stage: str, progress: int) -> None:
        """按需写入：阶段变化或进度涨幅达标才落存储。"""
        with self._lock:
            if self._closed:
                return
            stage_changed = stage != self._last_stage
            if not stage_changed and progress - self._last_progress < PROGRESS_STEP:
                return
            self._last_stage = stage
            self._last_progress = progress
            self._write(stage, progress)

    def bump(self, progress: int) -> None:
        """只推进进度（阶段不变）。"""
        with self._lock:
            if self._closed:
                return
            if progress - self._last_progress < PROGRESS_STEP:
                return
            self._last_progress = progress
            self._write(self._last_stage or "parse_link", progress)

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            self._closed = True

    # ---- 内部 ----

    def _write(self, stage: str, progress: int) -> None:
        """调用方需持有 self._lock。写入失败只记日志：进度不是任务成败的关键路径。"""
        self._last_write = time.monotonic()
        try:
            update_task(self._task_id, stage=stage, progress=progress)
        except Exception:  # pragma: no cover - 存储抖动不应打断转写
            logger.warning("任务进度写入失败（task_id=%s）", self._task_id, exc_info=True)

    def _heartbeat(self) -> None:
        while not self._stop.wait(self._interval):
            if self._closed:
                return
            self._fire_hook()
            with self._lock:
                if self._closed:
                    return
                if self._last_progress < 0:
                    continue  # 还没有任何进度上报，心跳无内容可刷
                if time.monotonic() - self._last_write < self._interval:
                    continue
                self._write(self._last_stage or "parse_link", self._last_progress)

    def _fire_hook(self) -> None:
        """心跳钩子（Worker 锁续期）：在锁外调用，避免阻塞进度写入；失败只记日志。"""
        if self._on_heartbeat is None:
            return
        try:
            self._on_heartbeat()
        except Exception:  # pragma: no cover - 续期失败不打断转写
            logger.warning("心跳钩子执行失败（task_id=%s）", self._task_id, exc_info=True)


def _finish(task_id: str, transcript: list[dict], source: str) -> None:
    items = [TranscriptItem(time=float(item["time"]), text=str(item["text"]).strip()) for item in transcript if str(item["text"]).strip()]
    plain_text = "".join(item.text for item in items)
    update_task(
        task_id,
        status="completed",
        stage="asr",
        progress=100,
        transcript=items,
        plain_text=plain_text,
        transcript_source=source,
    )


def _fail(task_id: str, exc: Exception) -> None:
    if isinstance(exc, AppException):
        message = exc.message
    else:
        message = "转写失败，请稍后重试"
    update_task(task_id, status="failed", error={"code": "TRANSCRIBE_FAILED", "message": message})


def run_transcription_pipeline(
    task_id: str,
    url: str | None,
    video: VideoInfo | None = None,
    prefer_subtitles: bool = True,
    hotwords: list[str] | None = None,
    engine: str | None = None,
    on_heartbeat: Callable[[], None] | None = None,
    byok_key: str | None = None,
    source: str = "url",
    file: FileRef | None = None,
) -> None:
    """后台转写主流程。任何异常都收敛为任务 failed + 友好信息。

    engine 为请求级手动选择（local | cloud，缺省由调用方按可用性自动挑，见 routers/transcribe.py）；
    云端运行期故障由 asr.transcribe_audio 内的 ASR_ENGINE_FALLBACK 降级本地，
    但降级目标本身不可用（如云端镜像没装 faster-whisper）时会直接抛错，不白跑一趟。

    on_heartbeat：心跳回调（S2-P2），Worker 用它续期执行锁；同步上下文调用。
    byok_key：用户自带的云端 ASR Key（见 services/byok.py），只对 cloud 引擎有意义。
    source/file（M3）：source="file" 时为本地文件任务，url 不参与流程，
    从 file.objectKey 指向的 OSS 对象取音频（见 _obtain_wav）。
    """
    reporter = ProgressReporter(task_id, on_heartbeat=on_heartbeat)
    try:
        if source == "file":
            wav = _obtain_wav_from_file(task_id, reporter, video, file)
        else:
            # 阶段一：字幕快路径（分层降级：yt-dlp CC → dm/view AI；秒级返回）
            reporter.set("parse_link", PROGRESS_PARSE)
            if prefer_subtitles:
                try:
                    subtitle_result = try_extract_subtitle_transcript(url)
                except Exception:
                    subtitle_result = None  # 字幕路径失败不阻断，回退 ASR
                if subtitle_result:
                    subtitle_items, subtitle_source = subtitle_result
                    # 字幕分句：B站 AI 字幕按口播节奏切成 11 字一条、零标点，
                    # 不合并的话前端分段逻辑失效（见 punctuate_subtitle_items 注释）。
                    # 放后端做，网页 / 桌面端 / SRT·VTT 导出 / plain_text 一次全受益。
                    subtitle_items = punctuate_subtitle_items(subtitle_items)
                    reporter.set("asr", PROGRESS_ASR_START)
                    reporter.close()
                    _finish(task_id, subtitle_items, subtitle_source)
                    return

            # 阶段二：下载音轨 + FFmpeg 管道转 16kHz 单声道 WAV（全程内存，S1-B）
            reporter.set("extract_audio", PROGRESS_EXTRACT_START)
            audio = download_audio(url)
            reporter.set("extract_audio", PROGRESS_EXTRACT_DONE)
            wav = convert_to_wav_16k_mono(audio)

        # 可选：人声分离（Demucs）。智能触发：非语音占比超阈值或显式开启；
        # 分离失败/未安装组件时降级回原音频，不使任务失败。分离后仍走 ASR 的 VAD 兜底链。
        from app.services import separation

        if separation.is_demucs_available():
            separate, non_speech_ratio = separation.should_separate(wav)
            if separate:
                try:
                    wav = separation.separate_vocals(wav)
                except Exception:
                    pass  # 降级：人声分离失败不影响转写主流程

        # 阶段三：ASR（本地 faster-whisper 或云端；音频以 BytesIO 传入）
        reporter.set("asr", PROGRESS_ASR_START)

        def on_asr_progress(ratio: float) -> None:
            reporter.bump(PROGRESS_ASR_START + int(ratio * PROGRESS_ASR_SPAN))

        # 上下文提示：热词（专有名词）+ 视频标题，引导 ASR 识别
        initial_prompt = video.title if video else None
        transcript = asr.transcribe_audio(
            wav,
            language="zh",
            progress_callback=on_asr_progress,
            initial_prompt=initial_prompt,
            hotwords=hotwords,
            engine=engine,
            byok_key=byok_key,
        )
        if not transcript:
            raise AppException("TRANSCRIBE_FAILED", "未能识别出语音内容", 500)
        reporter.close()
        _finish(task_id, transcript, "asr")
        if source == "file" and file is not None:
            # 成功才删对象；失败保留 ≤1 天（bucket 生命周期兜底），store.retry()
            # 重派作业时复用同一个 objectKey，用户不必重新上传几百 MB。
            storage.delete_object(file.objectKey)
    except Exception as exc:
        reporter.close()
        _fail(task_id, exc)
    finally:
        reporter.close()


def _obtain_wav_from_file(
    task_id: str,
    reporter: ProgressReporter,
    video: VideoInfo | None,
    file: FileRef | None,
):
    """本地文件任务的取音阶段：OSS 对象 → /tmp → ffmpeg 按路径转码 → WAV BytesIO。

    与 URL 路径的两点差异：
    - 源文件 500MB 级，**不进内存**：storage.download_to_temp 流式落盘（内网
      endpoint），ffmpeg 以文件路径为输入（stdout 管道读回的 WAV 才是内存对象）
    - 转码完成后按 WAV 字节数精确回填真实时长（16kHz × 2 字节 × 秒 = 32000/s），
      上传时拿不到可靠时长（浏览器探测只是预估）
    """
    if file is None or not (file.objectKey or "").strip():
        raise AppException("TRANSCRIBE_FAILED", "文件任务缺少文件引用", 500)
    reporter.set("extract_audio", PROGRESS_EXTRACT_START)
    with storage.download_to_temp(file.objectKey, FILE_MAX_SIZE_MB * 1024 * 1024) as source_path:
        reporter.bump(PROGRESS_EXTRACT_DONE)
        wav = convert_file_to_wav_16k_mono(source_path)
    duration_seconds = wav.getbuffer().nbytes // 32000
    if video is not None and duration_seconds > 0:
        try:
            update_task(task_id, video=video.model_copy(update={"duration": duration_seconds}))
        except Exception:  # pragma: no cover - 时长回填失败不影响转写
            logger.warning("真实时长回填失败（task_id=%s）", task_id, exc_info=True)
    return wav
