# 注：不使用外部 Dockerfile frontend（# syntax=docker/dockerfile:1）——本文件只用
# 标准语法（多阶段 / ARG / COPY --from），无需额外拉取 docker/dockerfile 镜像，
# 减少一次对 Docker Hub 的依赖（受限网络下更易构建成功）。
# =============================================================================
# 云端 ASR 后端镜像（S1-D）：Serverless / 阿里云函数计算（FC）等容器环境
#
# 设计要点：
#   1. 只装云端必需依赖（requirements-cloud.txt）：不含 faster-whisper / torch /
#      demucs / qwen-asr / funasr 等本地重型依赖（本机装 ≈ 数 GB，容器里不需要）
#   2. 静态 FFmpeg 单文件（johnvansickle 静态编译版，约 78MB）替代 apt 完整包（约 400MB）
#   3. 多阶段构建：ffmpeg 阶段取静态二进制、builder 阶段装 Python 依赖，
#      runtime 阶段只保留运行时产物
#   4. 业务配置（ASR_ENGINE / ASR_ENGINE_FALLBACK / TASK_STORE / REDIS_URL /
#      CLOUD_ASR_* / LLM_* / CORS_ORIGINS…）一律经环境变量注入，本文件不硬编码
#
# 构建（在 backend/ 目录）：
#   docker build -t video-to-text-backend:local .
# 运行：
#   docker run --rm -p 8000:8000 \
#     -e ASR_ENGINE=cloud -e CLOUD_ASR_API_KEY=sk-xxxx \
#     -e TASK_STORE=redis -e REDIS_URL=redis://host:6379/0 \
#     video-to-text-backend:local
#
# 注意：
#   - 阿里云 FC 需把「监听端口」配置为 8000（与下方 EXPOSE/CMD 一致）
#   - 云端无 faster-whisper：建议线上注入 ASR_ENGINE_FALLBACK=（空）禁用本地降级，
#     否则云端失败后的降级会报「本地语音识别组件未安装」（优雅失败，但无意义）
#   - 静态 FFmpeg（johnvansickle）为 GPLv3 许可，对外分发容器时须遵守 GPL
# =============================================================================

# ---- 阶段 1：静态 FFmpeg（仅取 ffmpeg 单文件；全仓库无 ffprobe 依赖）----
FROM debian:bookworm-slim AS ffmpeg
# TARGETARCH 由 BuildKit 自动注入（buildx / Docker Desktop 默认启用）；
# 经典构建器下取默认值 amd64。arm64 可显式传 --build-arg TARGETARCH=arm64
ARG TARGETARCH=amd64
ARG FFMPEG_URL=https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-${TARGETARCH}-static.tar.xz
# 可选：传入 tar.xz 的 sha256 做完整性校验（留空跳过）；形如 sha256sum 输出首列
ARG FFMPEG_SHA256=""
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl xz-utils ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL "$FFMPEG_URL" -o /tmp/ffmpeg.tar.xz \
 && if [ -n "$FFMPEG_SHA256" ]; then echo "$FFMPEG_SHA256  /tmp/ffmpeg.tar.xz" | sha256sum -c -; fi \
 && mkdir -p /tmp/ffmpeg \
 && tar -xJf /tmp/ffmpeg.tar.xz -C /tmp/ffmpeg --strip-components=1 \
 && install -m 0755 /tmp/ffmpeg/ffmpeg /usr/local/bin/ffmpeg \
 && rm -rf /tmp/ffmpeg /tmp/ffmpeg.tar.xz

# ---- 阶段 2：Python 依赖（独立 venv，便于整目录拷贝）----
FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements-cloud.txt ./
RUN pip install --upgrade pip \
 && pip install -r requirements-cloud.txt

# ---- 阶段 3：运行时（仅保留 venv + ffmpeg + 应用代码）----
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"
# ca-certificates：yt-dlp / httpx 访问 bilibili 与云端 ASR/LLM 的 HTTPS 需要系统 CA
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/venv /opt/venv
COPY --from=ffmpeg /usr/local/bin/ffmpeg /usr/local/bin/ffmpeg

# 非 root 运行；/app/data 供默认 SQLite 任务库写入（TASK_STORE=redis 时不用）
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /app/data \
 && chown -R appuser:appuser /app

WORKDIR /app
COPY --chown=appuser:appuser app ./app

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status == 200 else 1)"

# 默认启动 API 应用；Worker 函数（S2-P2）在同一镜像上覆盖启动命令即可：
#   uvicorn app.worker_app:app --host 0.0.0.0 --port 8000
# 两者都提供 GET /api/health，HEALTHCHECK 通用。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
