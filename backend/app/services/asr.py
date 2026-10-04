"""ASR 抽象：本地 faster-whisper（默认）与云端 OpenAI 兼容接口。

- local：首次调用懒加载模型（WHISPER_MODEL，默认 base / int8），线程安全；
  支持 initial_prompt（传入视频标题，引导专有名词识别）
- cloud：POST {ASR_API_BASE}/audio/transcriptions（multipart），
  response_format=verbose_json 拿分段时间戳；未配置 ASR_API_KEY 时报友好错误
- 输出统一过 postprocess_items 轻量清洗（折叠明显重复词、补句末标点）
"""
import re
import threading

import httpx

from app.config import (
    ASR_API_BASE,
    ASR_API_KEY,
    ASR_CLOUD_MODEL,
    ASR_PROVIDER,
    WHISPER_COMPUTE_TYPE,
    WHISPER_MODEL,
)
from app.errors import ParseFailedError

_LOCAL_MODEL = None
_LOCAL_MODEL_LOCK = threading.Lock()

ProgressCallback = None | object  # callable(ratio: float)

# 明显重复词：同一短 token（1-4 个汉字）紧邻连写 3 次以上，如 "我我我" → "我"
_REPEATED_CJK_TOKEN = re.compile(r"([\u4e00-\u9fff]{1,4})\1{2,}")
# 同一英文单词以空格分隔连写 3 次以上，如 "the the the" → "the"
_REPEATED_LATIN_TOKEN = re.compile(r"\b([A-Za-z]+)(?:\s+\1){2,}\b")
# 句末标点（缺失则按语言补齐：中文句号 / 英文句点）
_SENTENCE_END = re.compile(r"[。！？!?…，,、；;：:”」』)\].]$")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")


def postprocess_items(items: list[dict]) -> list[dict]:
    """轻量文本清洗：折叠明显重复词、补齐句末标点；不改时间戳与顺序。"""
    cleaned: list[dict] = []
    for item in items:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        text = _REPEATED_CJK_TOKEN.sub(r"\1", text)
        text = _REPEATED_LATIN_TOKEN.sub(r"\1", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        if len(text) >= 2 and not _SENTENCE_END.search(text):
            # 含中文补「。」，纯英文补「.」
            text += "。" if _HAS_CJK.search(text) else "."
        if text:
            cleaned.append({"time": item["time"], "text": text})
    return cleaned


def _get_local_model():
    global _LOCAL_MODEL
    with _LOCAL_MODEL_LOCK:
        if _LOCAL_MODEL is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise ParseFailedError("本地语音识别组件未安装（faster-whisper）") from exc
            _LOCAL_MODEL = WhisperModel(WHISPER_MODEL, device="cpu", compute_type=WHISPER_COMPUTE_TYPE)
    return _LOCAL_MODEL


def decode_audio_16k_mono(audio_path) -> "ndarray":
    """用 PyAV 解码任意音频为 16kHz 单声道 float32（-1~1）。

    不走 faster-whisper 内置的 av.open(metadata_errors=...) 解码：
    新版 PyAV（>=15）已移除该参数，直接传路径会在解码层崩掉。
    """
    import av
    import numpy as np

    container = av.open(str(audio_path))
    try:
        stream = next(s for s in container.streams if s.type == "audio")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        chunks: list = []
        for frame in container.decode(stream):
            for resampled in resampler.resample(frame):
                arr = resampled.to_ndarray()
                chunks.append(arr.reshape(-1) if arr.ndim > 1 else arr)
    finally:
        container.close()

    if not chunks:
        raise ParseFailedError("音频解码失败：未找到有效音轨")
    audio = chunks[0] if len(chunks) == 1 else np.concatenate(chunks)
    if audio.ndim > 1:
        audio = audio.mean(axis=0)
    return audio.astype("float32") / 32768.0


def transcribe_local(
    audio_path,
    language: str = "zh",
    progress_callback=None,
    initial_prompt: str | None = None,
) -> list[dict]:
    """faster-whisper 本地转写，返回 [{time, text}]；progress_callback 收 0~1 进度。

    - initial_prompt：视频标题等上下文提示，引导专有名词识别
    - 先开 VAD（过滤静音/背景音）；纯音乐等 VAD 全滤掉的场景自动去 VAD 重试
    """
    model = _get_local_model()
    audio = decode_audio_16k_mono(audio_path)
    prompt = (initial_prompt or "").strip() or None

    def run(vad_filter: bool):
        kwargs = {"language": language, "vad_filter": vad_filter}
        if prompt:
            kwargs["initial_prompt"] = prompt
        segments, info = model.transcribe(audio, **kwargs)
        items: list[dict] = []
        total = float(info.duration or 0)
        for segment in segments:
            text = segment.text.strip()
            if text:
                items.append({"time": round(float(segment.start), 2), "text": text})
            if progress_callback and total > 0:
                progress_callback(min(float(segment.end) / total, 1.0))
        return items

    items = run(True)
    if not items:
        items = run(False)
    return postprocess_items(items)


def transcribe_cloud(
    audio_path,
    language: str = "zh",
    progress_callback=None,
    initial_prompt: str | None = None,
) -> list[dict]:
    """OpenAI 兼容云端转写（verbose_json 带分段时间戳）。"""
    if not ASR_API_KEY:
        raise ParseFailedError("云端语音识别未配置：请设置 ASR_API_KEY 环境变量")

    audio_bytes = audio_path.read_bytes()
    data: dict = {
        "model": ASR_CLOUD_MODEL,
        "response_format": "verbose_json",
        "language": language,
    }
    prompt = (initial_prompt or "").strip()
    if prompt:
        data["prompt"] = prompt
    try:
        response = httpx.post(
            f"{ASR_API_BASE.rstrip('/')}/audio/transcriptions",
            headers={"Authorization": f"Bearer {ASR_API_KEY}"},
            files={"file": (audio_path.name, audio_bytes)},
            data=data,
            timeout=600,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ParseFailedError(f"云端语音识别失败（HTTP {exc.response.status_code}）") from exc
    except httpx.HTTPError as exc:
        raise ParseFailedError("云端语音识别服务连接失败，请稍后重试") from exc

    payload = response.json()
    segments = payload.get("segments") or []
    items: list[dict] = []
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if text:
            items.append({"time": round(float(segment.get("start", 0)), 2), "text": text})
    return postprocess_items(items)


def transcribe_audio(
    audio_path,
    language: str = "zh",
    progress_callback=None,
    initial_prompt: str | None = None,
) -> list[dict]:
    """按 ASR_PROVIDER 分发。"""
    if ASR_PROVIDER == "cloud":
        return transcribe_cloud(audio_path, language, progress_callback, initial_prompt)
    return transcribe_local(audio_path, language, progress_callback, initial_prompt)
