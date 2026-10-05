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

# ASR 引擎：local（faster-whisper 本地推理）| cloud（OpenAI 兼容 /audio/transcriptions）
# 兼容旧变量名 ASR_PROVIDER
ASR_ENGINE: str = os.getenv("ASR_ENGINE", os.getenv("ASR_PROVIDER", "local"))

# 本地模型：base（默认，下载约 140MB）/ small / tiny …
# 本地模型：默认 small（CER 评测后的本地最优平衡点，见 docs/asr-benchmark.md）；
# 追求速度用 base，追求准确用 medium / large-v3（large-v3 建议 GPU）
WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "small")
WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

# 云端 ASR（OpenAI 兼容协议）；未配置 ASR_API_KEY 时云端模式报友好错误
ASR_API_BASE: str = os.getenv("ASR_API_BASE", "https://api.openai.com/v1")
ASR_API_KEY: str = os.getenv("ASR_API_KEY", "")
ASR_CLOUD_MODEL: str = os.getenv("ASR_CLOUD_MODEL", "whisper-1")
