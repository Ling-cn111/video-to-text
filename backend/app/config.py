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
