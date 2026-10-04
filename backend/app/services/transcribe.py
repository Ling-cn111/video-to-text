"""转写流水线编排：字幕快路径 → 音频下载 → ASR，全程更新任务进度。

入口 run_transcription_pipeline 交由 FastAPI BackgroundTasks 在响应返回后执行。
"""
import shutil
from pathlib import Path

from app.config import AUDIO_DIR
from app.errors import AppException
from app.schemas import TranscriptItem, VideoInfo
from app.services import asr
from app.services.audio import convert_to_wav_16k_mono, download_audio
from app.services.subtitles import try_extract_subtitle_transcript
from app.services.tasks import update_task

# 各阶段进度锚点
PROGRESS_PARSE = 5
PROGRESS_EXTRACT_START = 15
PROGRESS_EXTRACT_DONE = 35
PROGRESS_ASR_START = 40
PROGRESS_ASR_SPAN = 55


def _finish(task_id: str, transcript: list[dict]) -> None:
    items = [TranscriptItem(time=float(item["time"]), text=str(item["text"]).strip()) for item in transcript if str(item["text"]).strip()]
    plain_text = "".join(item.text for item in items)
    update_task(task_id, status="completed", stage="asr", progress=100, transcript=items, plain_text=plain_text)


def _fail(task_id: str, exc: Exception) -> None:
    if isinstance(exc, AppException):
        message = exc.message
    else:
        message = "转写失败，请稍后重试"
    update_task(task_id, status="failed", error={"code": "TRANSCRIBE_FAILED", "message": message})


def run_transcription_pipeline(
    task_id: str,
    url: str,
    video: VideoInfo | None = None,
    prefer_subtitles: bool = True,
) -> None:
    """后台转写主流程。任何异常都收敛为任务 failed + 友好信息。"""
    work_dir = Path(AUDIO_DIR) / task_id
    try:
        # 阶段一：字幕快路径（CC / AI 字幕存在则秒级返回）
        update_task(task_id, stage="parse_link", progress=PROGRESS_PARSE)
        if prefer_subtitles:
            try:
                subtitle_items = try_extract_subtitle_transcript(url)
            except Exception:
                subtitle_items = None  # 字幕路径失败不阻断，回退 ASR
            if subtitle_items:
                update_task(task_id, stage="asr", progress=PROGRESS_ASR_START)
                _finish(task_id, subtitle_items)
                return

        # 阶段二：下载音轨 + FFmpeg 转 16kHz 单声道 WAV
        update_task(task_id, stage="extract_audio", progress=PROGRESS_EXTRACT_START)
        source = download_audio(url, work_dir)
        update_task(task_id, stage="extract_audio", progress=PROGRESS_EXTRACT_DONE)
        wav_path = convert_to_wav_16k_mono(source, work_dir)

        # 阶段三：ASR（本地 faster-whisper 或云端）
        update_task(task_id, stage="asr", progress=PROGRESS_ASR_START)

        def on_asr_progress(ratio: float) -> None:
            update_task(task_id, progress=PROGRESS_ASR_START + int(ratio * PROGRESS_ASR_SPAN))

        # 上下文提示：视频标题引导专有名词识别
        initial_prompt = video.title if video else None
        transcript = asr.transcribe_audio(
            wav_path, language="zh", progress_callback=on_asr_progress, initial_prompt=initial_prompt
        )
        if not transcript:
            raise AppException("TRANSCRIBE_FAILED", "未能识别出语音内容", 500)
        _finish(task_id, transcript)
    except Exception as exc:
        _fail(task_id, exc)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
