"""应用配置：CORS 与运行参数（均可通过环境变量覆盖）。"""
import os


def _split_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


# 精确白名单：前端本地开发地址，可用 CORS_ORIGINS 覆盖/追加
CORS_ORIGINS: list[str] = _split_origins(
    os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )
)

# Vercel 部署域（含 PR 预览域）用正则放行；生产域建议追加进 CORS_ORIGINS
CORS_ORIGIN_REGEX: str = os.getenv("CORS_ORIGIN_REGEX", r"https://.*\.vercel\.app")

# yt-dlp 提取信息的最长等待（秒），避免请求悬挂
EXTRACT_TIMEOUT_SECONDS: int = int(os.getenv("EXTRACT_TIMEOUT_SECONDS", "30"))

# ---- 转写（M2 阶段一）----

# 音频工作目录（下载 + 转换的临时文件，任务结束自动清理）
AUDIO_DIR = os.getenv("AUDIO_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), ".audio"))

# ASR 引擎：faster-whisper（本地，默认）| qwen3（Qwen3-ASR-1.7B 本地）| funasr（Fun-ASR-Nano 本地）
# | cloud（OpenAI 兼容接口）。兼容旧值：local = faster-whisper、旧变量名 ASR_PROVIDER
# 场景推荐：
#   追求速度     → faster-whisper + WHISPER_MODEL=base
#   平衡         → faster-whisper + WHISPER_MODEL=small
#   强 BGM/唱歌  → qwen3（Qwen3-ASR 官方支持 Songs with BGM，实测幻觉显著更低）
#   追求准确     → qwen3 或 large-v3（GPU）
#   高精度云端   → ASR_ENGINE=cloud
ASR_ENGINE: str = os.getenv("ASR_ENGINE", os.getenv("ASR_PROVIDER", "faster-whisper"))

# 主引擎失败时的自动降级引擎（qwen3/funasr 等新引擎失败时回退 faster-whisper；留空禁用）
ASR_ENGINE_FALLBACK: str = os.getenv("ASR_ENGINE_FALLBACK", "faster-whisper")

# 本地模型：base（默认，下载约 140MB）/ small / tiny …
# 本地模型：默认 small（CER 评测后的本地最优平衡点，见 docs/asr-benchmark.md）；
# 追求速度用 base，追求准确用 medium / large-v3（large-v3 建议 GPU）
WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "small")

# ---- 人声分离（Demucs，可选依赖 requirements-separation.txt）----

# 总开关：true 时无条件对转写音频做 Demucs 人声分离；false 时按下方阈值智能触发
ENABLE_VOCAL_SEPARATION: bool = os.getenv("ENABLE_VOCAL_SEPARATION", "false").lower() == "true"

# 智能触发阈值：VAD 统计「非语音时长占比」超过该值（BGM/音乐占比高）时自动开启人声分离
VOCAL_SEP_TRIGGER_RATIO: float = float(os.getenv("VOCAL_SEP_TRIGGER_RATIO", "0.4"))
WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

# 云端 ASR（OpenAI 兼容协议）；未配置 ASR_API_KEY 时云端模式报友好错误
ASR_API_BASE: str = os.getenv("ASR_API_BASE", "https://api.openai.com/v1")
ASR_API_KEY: str = os.getenv("ASR_API_KEY", "")
ASR_CLOUD_MODEL: str = os.getenv("ASR_CLOUD_MODEL", "whisper-1")
