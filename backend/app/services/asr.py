"""ASR 引擎抽象：本地 faster-whisper（默认）与云端 OpenAI 兼容接口。

- BaseASREngine：统一接口 transcribe(audio_path, language, progress_callback,
  initial_prompt, hotwords)——业务代码只依赖该接口，换引擎不改业务代码
- LocalWhisperEngine：faster-whisper（WHISPER_MODEL，默认 base / int8），懒加载线程安全；
  自定义 PyAV 解码（绕开新版 PyAV 的 av.open(metadata_errors=...) 不兼容）；
  Silero VAD 过滤纯音乐时自动去 VAD 重试
- CloudASREngine：OpenAI 兼容 /audio/transcriptions（verbose_json 分段时间戳）；
  ASR_API_KEY 未配置时报友好错误。通义听悟 / 火山引擎等非 OpenAI 协议的厂商
  可按同一接口新增引擎类
- ASR_ENGINE=local|cloud 切换（兼容旧变量名 ASR_PROVIDER）
- 输出统一过 postprocess_items：zhconv 繁→简、折叠明显重复词、按语言补句末标点
- initial_prompt：热词（hotwords）+ 视频标题组合，引导专有名词识别
"""
import re
import threading

import httpx
import zhconv

from app.config import (
    ASR_API_BASE,
    ASR_API_KEY,
    ASR_CLOUD_MODEL,
    ASR_ENGINE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_MODEL,
)
from app.errors import ParseFailedError

ProgressCallback = None | object  # callable(ratio: float)

# 明显重复词：同一短 token（1-4 个汉字）紧邻连写 3 次以上，如 "我我我" → "我"
_REPEATED_CJK_TOKEN = re.compile(r"([\u4e00-\u9fff]{1,4})\1{2,}")
# 同一英文单词以空格分隔连写 3 次以上，如 "the the the" → "the"
_REPEATED_LATIN_TOKEN = re.compile(r"\b([A-Za-z]+)(?:\s+\1){2,}\b")
# 句末标点（缺失则按语言补齐：中文句号 / 英文句点）
_SENTENCE_END = re.compile(r"[。！？!?…，,、；;：:”」』)\].]$")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")

_INITIAL_PROMPT_MAX_CHARS = 200


def build_initial_prompt(title: str | None, hotwords: list[str] | None) -> str | None:
    """组合热词与视频标题为 initial_prompt（热词优先，截断到 200 字符）。"""
    parts = [w.strip() for w in (hotwords or []) if w and w.strip()]
    if title and title.strip():
        parts.append(title.strip())
    prompt = "，".join(parts)[:_INITIAL_PROMPT_MAX_CHARS].strip()
    return prompt or None


def postprocess_items(items: list[dict]) -> list[dict]:
    """轻量文本清洗：繁→简、折叠明显重复词、补齐句末标点；不改时间戳与顺序。"""
    cleaned: list[dict] = []
    for item in items:
        text = zhconv.convert(str(item.get("text", "")), "zh-cn").strip()
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


class BaseASREngine:
    """ASR 引擎统一接口。"""

    name = "base"

    def transcribe(
        self,
        audio_path,
        language: str = "zh",
        progress_callback: ProgressCallback = None,
        initial_prompt: str | None = None,
        hotwords: list[str] | None = None,
    ) -> list[dict]:
        raise NotImplementedError


class LocalWhisperEngine(BaseASREngine):
    """faster-whisper 本地推理引擎。"""

    name = "local"

    def __init__(
        self,
        model_size: str = WHISPER_MODEL,
        compute_type: str = WHISPER_COMPUTE_TYPE,
        device: str = "cpu",
    ):
        self._model_size = model_size
        self._compute_type = compute_type
        self._device = device
        self._model = None
        self._model_lock = threading.Lock()

    def _get_model(self):
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    try:
                        from faster_whisper import WhisperModel
                    except ImportError as exc:
                        raise ParseFailedError("本地语音识别组件未安装（faster-whisper）") from exc
                    self._model = WhisperModel(self._model_size, device=self._device, compute_type=self._compute_type)
        return self._model

    def transcribe(
        self,
        audio_path,
        language: str = "zh",
        progress_callback: ProgressCallback = None,
        initial_prompt: str | None = None,
        hotwords: list[str] | None = None,
    ) -> list[dict]:
        model = self._get_model()
        audio = decode_audio_16k_mono(audio_path)
        prompt = build_initial_prompt(initial_prompt, hotwords)

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


class CloudASREngine(BaseASREngine):
    """OpenAI 兼容云端转写引擎（verbose_json 带分段时间戳）。"""

    name = "cloud"

    def transcribe(
        self,
        audio_path,
        language: str = "zh",
        progress_callback: ProgressCallback = None,
        initial_prompt: str | None = None,
        hotwords: list[str] | None = None,
    ) -> list[dict]:
        if not ASR_API_KEY:
            raise ParseFailedError("云端语音识别未配置：请设置 ASR_API_KEY 环境变量")

        prompt = build_initial_prompt(initial_prompt, hotwords)
        audio_bytes = audio_path.read_bytes()
        data: dict = {
            "model": ASR_CLOUD_MODEL,
            "response_format": "verbose_json",
            "language": language,
        }
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

        segments = response.json().get("segments") or []
        items: list[dict] = []
        for segment in segments:
            text = str(segment.get("text", "")).strip()
            if text:
                items.append({"time": round(float(segment.get("start", 0)), 2), "text": text})
        return postprocess_items(items)


_ENGINE: BaseASREngine | None = None
_ENGINE_LOCK = threading.Lock()


def get_engine() -> BaseASREngine:
    """按 ASR_ENGINE 返回引擎单例；新增云端厂商时在此注册新引擎类。"""
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            if ASR_ENGINE == "cloud":
                _ENGINE = CloudASREngine()
            else:
                _ENGINE = LocalWhisperEngine()
    return _ENGINE


def transcribe_audio(
    audio_path,
    language: str = "zh",
    progress_callback: ProgressCallback = None,
    initial_prompt: str | None = None,
    hotwords: list[str] | None = None,
) -> list[dict]:
    """业务入口（兼容旧调用方）。"""
    return get_engine().transcribe(audio_path, language, progress_callback, initial_prompt, hotwords)


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
