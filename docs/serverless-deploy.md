# 阿里云函数计算（FC）部署指南

> 适用镜像：`backend/Dockerfile`（PR #43 / S1-D，云端 ASR 专用）　作者：S1-E　日期：2026-10-21
>
> 关联文档：[backend/README.md § 容器化部署](../backend/README.md#容器化部署s1-d) · [ROADMAP](ROADMAP.md) · [asr-benchmark](asr-benchmark.md)

## 0. 结论先行

**当前版本可以上 FC，但只能覆盖「秒回」路径；异步转写不可用。**

| 能力 | FC 上是否可用 | 说明 |
| --- | --- | --- |
| `POST /api/parse`（yt-dlp 解析） | ✅ | 同步接口，1-2 秒返回 |
| `POST /api/summarize`（DeepSeek 总结） | ✅ | 同步接口，30-70 秒，需把函数超时调到 ≥ 90 秒 |
| 字幕快路径转写（B站 CC / AI 字幕，5-7 秒） | ⚠️ best effort | 在 `BackgroundTasks` 里执行，响应返回后实例可能被冻结，**不保证跑完** |
| 云端 ASR 转写（19-43 秒 / 150 秒音频） | ❌ | 同上，且耗时更长，实例几乎必然先被冻结 |
| 本地 ASR（faster-whisper） | ❌ | 镜像不含本地重型依赖（S1-D 设计） |

原因：`POST /api/transcribe` 用 FastAPI `BackgroundTasks` 在**响应返回之后**跑转写流水线（`backend/app/routers/transcribe.py` → `services/transcribe.py`）。FC 在请求结束、实例无活跃请求时会冻结/回收实例，后台任务随之停摆，任务会一直停在 `processing`。

**异步 ASR 上云需要 S2 的「独立函数触发」改造**（见 §8）。在那之前，请把本文档当作「解析 + 总结 + 字幕快路径」的上云方案。

---

## 1. 部署拓扑

```
浏览器
  │  同源 /backend-api/*
  ▼
Vercel（Next.js 前端，rewrites 代理）
  │  BACKEND_ORIGIN
  ▼
阿里云 FC 3.0 · 自定义容器（监听 9000）
  ├── yt-dlp / httpx ──→ bilibili.com（解析、字幕、音频）
  ├── httpx ──────────→ 硅基流动等云端 ASR（OpenAI 兼容）
  ├── httpx ──────────→ DeepSeek / 通义（LLM 总结）
  ├── redis.asyncio ──→ 阿里云 Redis（VPC 内网）：任务状态 + 限流计数
  └── 镜像拉取 ───────→ 阿里云 ACR（容器镜像服务）
```

前端不直接访问 FC：浏览器只请求同源 `/backend-api/*`，由 Vercel 上的 Next.js 服务端转发到 `BACKEND_ORIGIN`（见 `next.config.mjs`）。因此 **FC 的 HTTP 触发器可以不暴露给浏览器直连**，CORS 主要服务于本地调试。

---

## 2. 前置条件

| 项 | 要求 | 备注 |
| --- | --- | --- |
| 镜像架构 | **linux/amd64** | FC 不支持 arm64；详见 §3 |
| 镜像仓库 | 阿里云 ACR（个人版或企业版） | 与 FC 同地域延迟最低 |
| 任务存储 | `TASK_STORE=redis` + `REDIS_URL` | 多实例必需；SQLite 在 FC 上不可用 |
| Redis | 阿里云 Redis（或兼容服务） | 放在 VPC 内网，FC 需配 `vpcConfig` |
| FC 运行时 | `custom-container` | FC 3.0 + Serverless Devs `fc3` 组件 |
| 前端 | Vercel（或任意 Next.js 托管） | `BACKEND_ORIGIN` 指向 FC 触发器地址 |
| 账号权限 | RAM 子账号：FC 部署 + ACR 推送/拉取 | 不要用主账号 AccessKey |

---

## 3. 构建镜像（linux/amd64）

构建上下文是 `backend/`，不是仓库根目录（`backend/.dockerignore` 已排除 `.env.local`、`tests/`、`data/`）。

```bash
cd backend

# 确认 buildx 可用（Docker Desktop 默认自带）
docker buildx version

# 方式 A：经典 docker build（BuildKit 默认开启时 --platform 生效）
docker build --platform linux/amd64 \
  -t video-to-text-backend:v0.1.0 .

# 方式 B（推荐，尤其在 Apple Silicon / 老版本 Docker 上）：
# buildx 显式指定平台，--load 把结果导回本地镜像列表
docker buildx build --platform linux/amd64 \
  -t video-to-text-backend:v0.1.0 --load .

# 可选：校验静态 FFmpeg 包的 sha256（留空则跳过校验）
docker buildx build --platform linux/amd64 \
  --build-arg FFMPEG_SHA256=<sha256> \
  -t video-to-text-backend:v0.1.0 --load .
```

**必须校验架构**，否则会得到一个能 push、但在 FC 上起不来的镜像：

```bash
docker image inspect --format '{{.Os}}/{{.Architecture}}' video-to-text-backend:v0.1.0
# 期望输出：linux/amd64
```

常见坑：

- Apple Silicon 上不加 `--platform` 会构建出 `linux/arm64`，FC 启动时报 `exec format error`。
- 老版本 Docker 的经典构建器会**忽略** `--platform` 并打印警告，务必用 `buildx`。
- 镜像实测约 **406MB**（S1-D 记录）。首次冷启动会受镜像拉取时间影响，生产建议开 ACR 镜像加速或使用企业版。

本地自检（镜像默认监听 8000，`docker run` 里映射到 8000）：

```bash
docker run --rm -p 8000:8000 \
  -e ASR_ENGINE=cloud \
  -e CLOUD_ASR_API_KEY=sk-xxxx \
  -e ASR_ENGINE_FALLBACK= \
  -e TASK_STORE=redis \
  -e REDIS_URL=redis://host:6379/0 \
  -e DEEPSEEK_API_KEY=sk-xxxx \
  video-to-text-backend:v0.1.0

curl -s localhost:8000/api/health        # {"status":"ok"}
curl -s localhost:8000/api/capabilities  # {"cloudAsrConfigured":true,"summarizeConfigured":true}
```

---

## 4. 推送到阿里云 ACR

先在 **容器镜像服务控制台** 创建命名空间与镜像仓库（个人版免费；生产建议企业版，支持 VPC 域名与更稳定的拉取带宽）。

```bash
# 1) 登录（用户名是阿里云账号全名，不是 RAM 子账号名时也可用子账号 + 固定密码）
docker login --username=<阿里云账号> registry.cn-hangzhou.aliyuncs.com

# 2) 打标签：<registry>/<namespace>/<repo>:<tag>
docker tag video-to-text-backend:v0.1.0 \
  registry.cn-hangzhou.aliyuncs.com/<namespace>/video-to-text-backend:v0.1.0

# 3) 推送
docker push registry.cn-hangzhou.aliyuncs.com/<namespace>/video-to-text-backend:v0.1.0
```

镜像地址按 ACR 版本不同：

| 版本 | 公网地址 | 内网（VPC）地址 |
| --- | --- | --- |
| 个人版 | `registry.<region>.aliyuncs.com/<ns>/<repo>:<tag>` | 同左（个人版无独立 VPC 域名） |
| 企业版 | `<instance>-registry.<region>.cr.aliyuncs.com/<ns>/<repo>:<tag>` | `<instance>-registry-vpc.<region>.cr.aliyuncs.com/...` |

建议：

- **同地域**：FC 与 ACR 选同一 region（如都用 `cn-hangzhou`），跨地域拉取更慢且可能失败。
- FC 支持「ACR 镜像免密拉取」，可避免在 s.yaml 里写镜像仓库密码；若用私有仓库且不免密，需在 `customContainerConfig.registryConfig.authConfig` 配置账号密码（**不要提交明文到 git**）。
- 用企业版 VPC 地址时，FC 必须配置同一 VPC 的 `vpcConfig`，否则拉不到镜像。

---

## 5. FC 配置：`s.yaml` 双函数模板

仓库根目录的 **`s.yaml`** 就是完整可用的模板（`edition: 3.0.0`，Serverless Devs **fc3** 组件，字段已对照 `devsapp/fc3` 的 `ICustomContainerConfig` / `IAsyncInvokeConfig` schema）。它定义两个函数：

| 函数 | 启动命令 | 职责 | 规格 |
| --- | --- | --- | --- |
| `video-to-text-api` | `uvicorn app.main:app` | 解析 / 总结 / 字幕快路径 / **投递异步作业** | 1C2G / 120s / 并发 10 |
| `video-to-text-worker` | `uvicorn app.worker_app:app` | 下载 + ASR（长任务） | 2C4G / 1800s / 并发 1 |

两者用**同一个镜像**，只在 `customContainerConfig.command` 上区分 ASGI 应用。

### 5.1 两段式部署（重要）

API 函数需要 worker 的触发器地址（`FC_WORKER_URL`），而该地址要等 worker 创建后才有，且 `fcapp.run` 子域含随机串、无法由 region + 函数名推导。因此：

```bash
# ---- 第一段：先建出两个函数（FC_WORKER_URL 留空不阻断启动，只是分发会失败）----
s deploy -t s.yaml

# ---- 取 worker 的 HTTP 触发器地址 ----
s info -t s.yaml
# 输出里 worker 函数下的 URL 形如：
#   https://video-to-text-worker.abc123def.cn-hangzhou.fcapp.run

# ---- 第二段：把该地址写进部署机环境变量后重新部署 ----
export FC_WORKER_URL=https://video-to-text-worker.abc123def.cn-hangzhou.fcapp.run
s deploy -t s.yaml
```

> `FC_WORKER_URL` 漏配时应用**仍能启动**（不阻断引导），但每次 `POST /api/transcribe` 都会明确失败：任务被标 `failed`（`TRANSCRIBE_FAILED`「任务分发失败，请重试」）+ 返回 500。这是刻意设计——宁可明确报错，也不静默丢任务。

### 5.2 关键字段说明

- **`port` 必须等于 uvicorn 实际监听端口**（模板统一用 9000）。端口不一致的典型表现是函数部署成功但请求 502/超时、健康检查一直失败。
- **`entrypoint` / `command` 语义与 Docker 一致**：`entrypoint` 是可执行文件（`uvicorn`），`command` 是参数。镜像本身 `CMD` 监听 8000，模板通过这两个字段覆盖为 9000。
- **`instanceConcurrency: 1`（worker）**：一实例一作业，内存模型最简单。100MB 音频全程在内存 + ffmpeg 转码，并发跑多作业容易 OOM。
- **`timeout: 1800`（worker）**：长视频 ASR 需要。请对照当前地域配额确认上限，超限需在配额中心申请。
- **`TASK_INIT_MODE`**：api = `recover`，worker = **`none`**。Worker 每次冷启动都跑初始化，`recover` 会把其他实例正在跑的任务误判为 `interrupted`（代码里也显式传 `recover_interrupted=False`，双保险）。
- **`asyncInvokeConfig`**：`maxAsyncRetryAttempts: 3`（FC 默认）、`maxAsyncEventAgeInSeconds: 604800`（与 `TASK_TTL_SECONDS` 7 天对齐）。
- **`authType: anonymous` + `WORKER_TOKEN`**：官方文档明确「无需认证时可在业务中校验请求头 `Authorization` 是否合法以自行鉴权」。worker 的触发器只开 `POST`，鉴权由应用层 `WORKER_TOKEN` 兜底。若需更严可改 `authType: function`（签名认证），但会重新引入签名 SDK——这正是 P3 刻意规避的。
- **内置域名仅用于测试**：官方说明 `*.fcapp.run` 只适合联调，对外服务应绑已备案的自定义域名。上线前请替换。
- **`code: ./backend`**：自定义容器（`runtime: custom-container`）**只使用镜像**，fc3 在部署前会主动 `unset` 掉 `code`（见 `resources/fc/impl/replace-function-config.ts`），代码目录不会被打进镜像。保留该字段只是为了将来若切回非容器运行时可直接复用，删掉也不影响部署。

### 5.3 环境变量怎么注入

`s.yaml` 里所有密钥都写成 `${env('NAME')}`，由部署机环境变量提供，仓库不含明文：

```bash
export REDIS_URL=redis://:password@r-xxxx.redis.rds.aliyuncs.com:6379/0
export CLOUD_ASR_API_KEY=sk-xxxx
export DEEPSEEK_API_KEY=sk-xxxx
export WORKER_TOKEN=$(openssl rand -hex 24)
export CORS_ORIGINS=https://your-frontend.vercel.app
export FC_WORKER_URL=https://video-to-text-worker.abc123def.cn-hangzhou.fcapp.run
s deploy -t s.yaml
```

> **语法必须带引号**：Serverless Devs 的魔法变量是 `${env('NAME')}` / `${env('NAME', '默认值')}`（内部按 JS 表达式求值）。写成 `${env(NAME)}` 会抛 `NAME is not defined`，`s deploy` 直接失败。模板里 `FC_WORKER_URL` / `WORKER_TOKEN` / `CORS_ORIGINS` 都带了默认值，缺省不会报错。

控制台等价路径：函数计算 3.0 → 创建函数 → **自定义容器** → 选择 ACR 镜像 → 监听端口填 9000 → 配置环境变量 / VPC / 实例规格 → 创建 HTTP 触发器（api 开 GET+POST，worker 只开 POST）。

---

## 6. 环境变量清单

### 6.1 必填（4 项 + `ENQUEUE_MODE=fc` 时多 1 项）

| 变量 | 示例 | 说明 |
| --- | --- | --- |
| `TASK_STORE` | `redis` | 必须。`memory` 会写实例内 SQLite（`/app/data`），实例回收即丢，且多实例互不可见 |
| `REDIS_URL` | `redis://:password@r-xxxx.redis.rds.aliyuncs.com:6379/0` | 必须。任务状态 + 限流共享计数。未配置会**静默降级** memory 并打警告 |
| `CLOUD_ASR_API_KEY` | `sk-xxxx` | 必须。未配置时提交云端转写直接报 `CLOUD_NOT_CONFIGURED` |
| `DEEPSEEK_API_KEY` | `sk-xxxx` | 必须（总结）。未配置时 `/api/summarize` 报 `SUMMARIZE_NOT_CONFIGURED` |
| `FC_WORKER_URL` | worker 的 `*.fcapp.run` 地址 | **仅 API 函数、仅 `ENQUEUE_MODE=fc` 时必填**（见 §6.2） |

### 6.2 建议配置

| 变量 | 建议值 | 原因 |
| --- | --- | --- |
| `ASR_ENGINE` | `cloud` | 镜像不含本地 ASR，默认走云端 |
| `ASR_ENGINE_FALLBACK` | 空 | 否则云端失败后会尝试本地 faster-whisper，报「本地语音识别组件未安装」（优雅失败但无意义） |
| `LLM_PROVIDER` | `deepseek` | 或用 `qwen` + `DASHSCOPE_API_KEY` |
| `CORS_ORIGINS` | 你的正式域名 | 默认只放行 localhost；Vercel 预览域已由 `CORS_ORIGIN_REGEX` 覆盖 |
| `RATE_LIMIT_PER_MINUTE` | `10`（0 = 关闭） | 配置 Redis 后为全实例共享配额 |
| `MAX_AUDIO_SIZE_MB` | `100` | 与 `memorySize` 匹配；过大易 OOM |
| `TASK_TTL_SECONDS` | `604800`（7 天） | 任务 JSON 含完整文字稿；0 = 永久保留（不推荐，Redis 会无限增长） |
| `TASK_INIT_MODE` | `recover`（API）/ `none`（Worker） | Worker 必须 `none`，否则每次冷启动会把其他实例在跑的任务判为中断 |
| `TASK_STALE_SECONDS` | `600` | 超过该时长无心跳写入才判为遗留任务；须明显大于心跳间隔 |
| `TASK_HEARTBEAT_SECONDS` | `30` | 长阻塞步骤（下载 / FFmpeg / ASR）靠心跳保活；0 = 关闭 |
| `ENQUEUE_MODE` | `fc`（API）/ `inline`（Worker） | 任务分发模式。`inline` = 同进程 BackgroundTasks；`fc` = 投递到 worker 触发器（异步）；`mns`/`redis` 为 P4 实现位，当前显式报错 |
| `FC_WORKER_URL` | worker 的 `*.fcapp.run` 地址 | **`ENQUEUE_MODE=fc` 时必填**。`s info -t s.yaml` 里取；无法由 region + 函数名推导 |
| `FC_WORKER_PATH` | `/internal/worker/transcribe` | worker 上的作业路由，须与 `WORKER_PATH` 一致 |
| `FC_ASYNC_TIMEOUT_SECONDS` | `5` | 异步投递超时：只等 FC 回 202，不等作业跑完 |
| `TASK_LOCK_SECONDS` | `3600` | Worker 执行锁 TTL（`SET NX EX`），执行期间按心跳续期；崩溃后到期自动释放 |
| `WORKER_TOKEN` | 空 | Worker 端点鉴权；非空时要求 `X-Worker-Token` 或 `Authorization: Bearer <token>` 一致 |
| `CLOUD_ASR_BASE_URL` / `CLOUD_ASR_MODEL` | 见 `.env.example` | 换服务商/模型时覆盖 |

`TASK_DB_PATH`、`WHISPER_*`、`ENABLE_VOCAL_SEPARATION`、`BILI_COOKIE` 在 FC 上都不需要（`BILI_COOKIE` 为预留能力，当前未实现）。

> **不需要** `FC_ACCOUNT_ID` / `FC_REGION` / `FC_WORKER_FUNC_NAME` / `ALIBABA_CLOUD_ACCESS_KEY_*`：这些只在走 OpenAPI `InvokeFunction`（需 ACS3 签名）时才需要。P3 走 HTTP 触发器 + `X-Fc-Invocation-Type: Async`，不签名、不常驻 AK/SK。

### 6.3 密钥管理：建议走 KMS

**不要把 Key 写进 s.yaml 提交到 git。** 分层建议：

1. **部署机侧（当前即可用）**：s.yaml 用 `${env('REDIS_URL')}` / `${env('CLOUD_ASR_API_KEY')}` 引用部署机环境变量，由 CI 或本地 shell 注入；仓库里只保留占位符。这是本文示例采用的方式。
2. **KMS 凭据管家（生产推荐）**：在 KMS / 凭据管家创建凭据，函数执行角色只授予读取该凭据的权限（最小权限），部署流水线从 KMS 取值后注入 FC 环境变量。这样密钥不落在仓库、不落在开发者机器上。
3. **运行时拉取**：函数启动时用阿里云 SDK 调 `GetSecretValue` 再写入 `os.environ`。**本仓库当前没有内置 KMS 客户端**，这属于 S2 的应用层改造；在改造前，请用方案 1/2，并接受「FC 控制台里能看到明文环境变量」这一事实——用 RAM 权限和操作审计兜底。

补充：环境变量在 FC 控制台/API 中可被有权限的 RAM 用户读取，务必限制 `fc:UpdateFunction` / `fc:GetFunction` 的授权范围，并开启操作审计。KMS 的具体字段与授权策略以当前 FC / KMS 官方文档为准（本仓库未做端到端验证）。

---

## 7. 部署后验收

### 7.1 基础连通

```bash
API_URL=https://video-to-text-api.<随机串>.cn-hangzhou.fcapp.run
WORKER_URL=https://video-to-text-worker.<随机串>.cn-hangzhou.fcapp.run

# 1) 两个函数都活着
curl -s $API_URL/api/health              # {"status":"ok"}
curl -s $WORKER_URL/api/health           # {"status":"ok","role":"worker"}

# 2) Key 是否注入成功（只返回布尔，不含 Key）
curl -s $API_URL/api/capabilities        # {"cloudAsrConfigured":true,"summarizeConfigured":true}

# 3) 解析（同步，1-2 秒）
curl -s -X POST $API_URL/api/parse \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.bilibili.com/video/BV1xxxxxxxxx"}'
```

### 7.2 异步链路验收（S2-P3 核心）

```bash
# 1) 先单独验证 worker：触发器 + token + 异步头
#    期望 HTTP 202 + X-Fc-Request-Id；worker 日志出现「Worker 收到未知任务（task_id=probe-1）」
#    —— 说明事件体已正确抵达容器（这是验证「异步是否透传 path」的关键一步）
curl -i -X POST "$WORKER_URL/internal/worker/transcribe" \
  -H "Content-Type: application/json" \
  -H "X-Worker-Token: $WORKER_TOKEN" \
  -H "X-Fc-Invocation-Type: Async" \
  -d '{"task_id":"probe-1","url":"https://www.bilibili.com/video/BV1xxxxxxxxx","video":null,"prefer_subtitles":true,"hotwords":null,"engine":"cloud"}'

# 2) 全链路：创建任务 → 轮询
TASK=$(curl -s -X POST $API_URL/api/transcribe \
  -H 'Content-Type: application/json' \
  -d '{"videoId":"BV1xxxxxxxxx","engine":"cloud"}' | jq -r .taskId)

for i in $(seq 1 120); do
  curl -s "$API_URL/api/transcribe/$TASK" | jq -c '{status,stage,progress,error}'
  sleep 2
done
```

**判读表：**

| 现象 | 定位 |
| --- | --- |
| 第 1 步返回 `202` + `X-Fc-Request-Id` | 触发器与异步头都正确，事件已入队 |
| 第 1 步返回 `404` | 异步调用**没有透传 path**（见 §8.4 R1），需把 `FC_WORKER_PATH` 改成实际收到的路径 |
| 第 1 步返回 `401` | api/worker 两侧 `WORKER_TOKEN` 不一致 |
| 第 1 步返回 `403` / `access denied` | 触发器认证方式不是 `anonymous` |
| 第 2 步一直 `processing` 且 `attempts` 不增 | api 侧投递失败，查 api 日志的 `FC 投递被拒` / `FC 投递失败` |
| 第 2 步 `attempts` 增长但 `stage` 不动 | worker 侧问题，查 worker 日志 |
| 第 2 步变 `completed` 且 `transcriptSource` 为 `subtitle_cc` / `subtitle_ai` / `asr` | 全链路打通 |
| 任务 `failed`（「任务分发失败，请重试」） | `FC_WORKER_URL` 漏配或写错，见 §5.1 |

> `attempts` 字段在 `GET /api/transcribe/{taskId}` 的响应里不返回（前端契约未变），需查 Redis `task:{taskId}` 的 JSON。

### 7.3 日志

```bash
s logs -t s.yaml --function-name video-to-text-api --tail
s logs -t s.yaml --function-name video-to-text-worker --tail
```

优先看：容器启动日志（uvicorn 是否 listen 成功）、`redis` 降级警告、`已投递到 FC（task_id=..., request_id=...）`。

### 7.4 前端切换

```bash
# Vercel 环境变量
BACKEND_ORIGIN=https://video-to-text-api.<随机串>.cn-hangzhou.fcapp.run
NEXT_PUBLIC_USE_MOCK=false        # 构建期常量，改完必须重新构建
```

然后在 Vercel 重新部署，打开首页确认后端可达横幅消失、解析/字幕/长转写流程正常。

---

## 8. 已知限制（S2 预埋）

### 8.1 BackgroundTasks 在 Serverless 下不可靠（已在 P3 解决）

- **改造前**：`POST /api/transcribe` 把 `run_transcription_pipeline` 交给 FastAPI `BackgroundTasks`，在 HTTP 响应返回后执行。
- **FC 行为**：请求结束且实例无活跃请求后，实例会被冻结/回收，后台协程/线程不再获得 CPU 时间。
- **后果**：任务长期停在 `processing`（`stage` 可能停在 `parse_link` / `extract_audio` / `asr`），前端无限轮询。
- **S2-P3 起的解法**：`ENQUEUE_MODE=fc` 把作业投递到独立的 worker 函数（异步调用），API 函数响应完就不再关心执行；worker 实例有自己的生命周期，不受原请求约束。
- **仍需注意**：`ENQUEUE_MODE=inline` 时上述限制**依然存在**。本地开发与单机部署用 inline 没问题，云端必须用 `fc`。

### 8.2 其他限制

| 限制 | 影响 | 规避/说明 |
| --- | --- | --- |
| 仅支持 amd64 | arm64 镜像启动即崩 | 构建时固定 `--platform linux/amd64` |
| 无持久磁盘 | SQLite / 本地文件不跨实例、不跨重启 | 必须 `TASK_STORE=redis`；只信 `/tmp` 且即用即删（S1-B 已去磁盘化） |
| 多实例 | 内存态任务/限流不共享 | 同上，任务与限流都走 Redis |
| 内存上限 | 音频全程在内存（默认上限 100MB） | api `memorySize` ≥ 2048MB、worker 4096MB；按需调低 `MAX_AUDIO_SIZE_MB` |
| 函数超时 | 同步总结 30-70 秒；worker 长转写 | api `timeout` ≥ 120 秒；worker `timeout` 1800 秒（超限需申请配额） |
| 冷启动 + 406MB 镜像 | 首个请求慢 | ACR 同地域 / 镜像加速；必要时预留实例 |
| 静态 FFmpeg 为 GPLv3 | 对外分发镜像需遵守 GPL | 保留许可声明；如不可接受，改用 apt 版 FFmpeg（镜像约 +400MB） |
| 无本地 ASR | `ASR_ENGINE=local` 不可用 | 固定 `cloud`，`ASR_ENGINE_FALLBACK` 留空 |
| B站风控 / yt-dlp 失效 | 解析或字幕失败 | 定期重建镜像升级 `yt-dlp`；关注 `EXTRACT_TIMEOUT_SECONDS` |
| Redis 任务无 TTL | 任务记录无限增长 | **S2-P0 已修**：`TASK_TTL_SECONDS`（默认 7 天，滑动续期）；0 = 永久保留 |
| 冷启动即改写任务状态 | 多实例下误杀在途任务 | **S2-P0 已修**：只把「陈旧」（`TASK_STALE_SECONDS` 内无心跳）的 `processing` 判为 `interrupted`；Worker 侧设 `TASK_INIT_MODE=none` |
| 异步调用 Body ≤ 128 KB | 事件体过大会被 FC 拒收 | `TranscribeJob.to_dict()` 仅几百字节；**不要往事件体里塞文字稿**（P1 只传入参的设计已保证） |
| Worker 崩溃后重试被锁挡住 | 任务停在 `processing` | **P3 已知缺口**：FC 重试间隔 0.5/1/2 秒，而 `TASK_LOCK_SECONDS=3600`，3 次重试都会撞锁返回 `skipped`。恢复依赖 **P4 reaper**（按 `TASK_STALE_SECONDS=600` 判 `interrupted`）。P3 只承诺「投递可靠 + 正常路径幂等」 |
| 内置 `*.fcapp.run` 域名仅用于测试 | 生产稳定性无保障 | 官方明确建议绑已备案自定义域名；上线前替换 `FC_WORKER_URL` 与前端 `BACKEND_ORIGIN` |

### 8.3 S2 改造方向：独立函数触发

目标：让异步 ASR 不再依赖「响应后的后台任务」。

1. **拆成两个函数**
   - `api` 函数（本文镜像）：只处理解析、总结、字幕快路径；`POST /api/transcribe` 改为**写任务 + 投递消息**后立即返回。
   - `worker` 函数（同一镜像，另加入口/命令）：消费队列，执行下载 + ASR，写回 Redis 任务状态。
2. **触发方式（已选定）**
   - **HTTP 触发器 + 请求级异步**（P3 采用）：`api` 用 `httpx` POST 到 worker 的触发器地址并带
     `X-Fc-Invocation-Type: Async`，FC 立即回 202 并落内部队列。**不需要 ACS3 签名、不需要 AK/SK、不需要 SDK**；
   - 备选：FC **OpenAPI 异步调用**（`InvokeFunction` + 签名）——需要 RAM 授权与常驻 AK/SK，仅当不允许配触发器时考虑；
   - 规模化后：**MNS / RocketMQ 队列触发**（P4），天然带重试、削峰与死信队列。
3. **状态与幂等**：任务状态继续放 Redis（`TASK_STORE=redis` 已具备），worker 按 `taskId` 幂等更新；补 TTL 与失败重试上限。
   > **S2-P0 已完成**：`TASK_TTL_SECONDS`（默认 7 天滑动续期）、`TASK_INIT_MODE=none`（Worker 启动不改写状态）、`attempts` 字段、进度心跳（`TASK_HEARTBEAT_SECONDS`）与写入节流、同步桥接连接泄漏修复。
   > **S2-P1 已完成**：`services/dispatcher.py` + `TranscribeJob`（完整作业体，可 `to_dict()` 作 FC 事件 payload）+ `ENQUEUE_MODE=inline|fc|mns|redis`（默认 inline，行为不变；未实现模式显式报错）。
   > **S2-P2 已完成**：`app/worker_app.py` 独立 ASGI 应用（`POST /internal/worker/transcribe`）——不挂限流/CORS、不 import `app.main`，启动 `init(recover_interrupted=False)`；`SET task:lock:{id} NX EX` 幂等领取 + 心跳续期 + WATCH 比对释放；pipeline 经 `asyncio.to_thread` 执行（同步实现不能直接 await）；业务失败回 200、基础设施异常回 5xx。
   > **S2-P3 已完成**：`ENQUEUE_MODE=fc` 真正可用——`FcDispatcher` 用 `httpx` 把 `TranscribeJob` POST 到 worker 函数的 **HTTP 触发器**，带 `X-Fc-Invocation-Type: Async`（FC 立即回 202 并落内部队列）。**不签名、不引 SDK、不常驻 AK/SK**；`WORKER_TOKEN` 双向校验；worker 新增 `POST /invoke` 别名（`InvokeFunction` 兼容位）；`s.yaml` 双函数模板入库。
   > 仍待 P4：超时收割（reaper）、`TASK_TIMEOUT` 错误码、MNS/Redis 队列模式。
4. **前端零改动**：仍是 `POST /api/transcribe` + `GET /api/transcribe/{taskId}` 轮询，契约不变。
5. **超时/规格**：worker 函数单独配置更长超时、更大内存；`api` 函数保持小规格低成本。

Worker 函数在 FC 侧只需覆盖启动命令，**同一镜像无需改动**：

```bash
# api 函数（默认 CMD）
uvicorn app.main:app --host 0.0.0.0 --port 9000
# worker 函数：只换 ASGI 应用
uvicorn app.worker_app:app --host 0.0.0.0 --port 9000
```

环境变量差异：worker 函数设 `TASK_INIT_MODE=none`（双保险，代码已显式传 `False`）、
`TASK_LOCK_SECONDS`（默认 3600）、`WORKER_TOKEN`；`api` 函数保持 `TASK_INIT_MODE=recover`、
`ENQUEUE_MODE=fc`、`FC_WORKER_URL`。完整对照见 §6.2 与仓库根目录 `s.yaml`。

### 8.4 P3 遗留与真机待验证项

- **R1（待真机确认）异步调用是否原样透传 path**：官方只写明「将请求持久化保存，然后立即返回响应」，没有一句明确说异步重放时 path 保持不变。同步透传模式是明确的，异步极可能一致。§7.2 第 1 步的 `curl` 就是专门验证这一点——若返回 404，把 `FC_WORKER_PATH` 改成实际收到的路径即可（代码侧零改动）。
- **R2（已确认）异步 Body 上限 128 KB**：`TranscribeJob.to_dict()` 仅几百字节，余量充足。**不要往事件体里塞文字稿**——P1 只传作业入参的设计已规避。
- **R3（已知缺口，等 P4）崩溃恢复**：worker 被 OOM/超时杀掉后，FC 的 3 次重试（间隔 0.5/1/2 秒）都会撞上 `TASK_LOCK_SECONDS=3600` 的锁并返回 `skipped`，任务停在 `processing` 直到 P4 reaper 按 `TASK_STALE_SECONDS` 判定。P3 只承诺「投递可靠 + 正常路径幂等」。

在此之前，请不要把 FC 版本用于「无字幕视频」的生产流量——除非已切到 `ENQUEUE_MODE=fc` 并完成 §7.2 验收。

---

## 9. 故障排查

| 现象 | 常见原因 | 处理 |
| --- | --- | --- |
| 启动失败：`exec format error` | 镜像不是 amd64 | 重新用 `--platform linux/amd64` 构建并校验 `docker image inspect` |
| 拉取镜像失败 / `ImagePullBackOff` | ACR 地址错、无权限、跨地域、VPC 未打通 | 检查镜像地址与 tag；配置免密拉取或 `registryConfig`；企业版 VPC 地址需配 `vpcConfig` |
| 请求 502 / 健康检查失败 | 端口不一致；uvicorn 未监听 `0.0.0.0`；启动即崩 | 对齐 `port` 与 `--port`；看启动日志；本地 `docker run` 复现 |
| 请求超时 / 504 | `timeout` 太小（总结 30-70s） | 调到 120s 以上 |
| 任务一直 `processing` | `ENQUEUE_MODE=inline` 下 BackgroundTasks 被冻结；或 `fc` 模式投递失败 | 云端切 `ENQUEUE_MODE=fc`（§5、§7.2）；查 api 日志的 `FC 投递被拒` / `FC 投递失败` |
| api 日志 `FC 投递被拒（HTTP 404）` | `FC_WORKER_URL` 少了路径，或异步未透传 path | 检查 `FC_WORKER_URL` + `FC_WORKER_PATH`；参考 §8.4 R1 |
| api 日志 `FC 投递被拒（HTTP 401）` | `WORKER_TOKEN` 两侧不一致 | 让 api 与 worker 用同一个 `WORKER_TOKEN` |
| api 日志 `FC 投递被拒（HTTP 403）` | 触发器认证方式不是 `anonymous` | 改触发器认证方式，或改用 Bearer/签名 |
| 任务 `failed`（「任务分发失败，请重试」） | `FC_WORKER_URL` 漏配 / 写错 / FC 拒收 | 见 §5.1 两段式部署 |
| 429 `RATE_LIMITED` | 触发限流 | 调 `RATE_LIMIT_PER_MINUTE`；确认 Redis 生效（否则各实例各自计数） |
| `CLOUD_NOT_CONFIGURED` | 未注入 `CLOUD_ASR_API_KEY` | 检查环境变量与 `s info` |
| `SUMMARIZE_NOT_CONFIGURED` | 未注入 `DEEPSEEK_API_KEY` | 同上；qwen 用 `DASHSCOPE_API_KEY` |
| 日志出现 Redis 降级警告 | `REDIS_URL` 不可达 / 密码错 / 未配 VPC | 用 VPC 内网地址 + 安全组放行；确认 `redis` 包在镜像内（`requirements-cloud.txt` 已含） |
| `AUDIO_TOO_LARGE` | 超过 `MAX_AUDIO_SIZE_MB` | 调大上限并同步调大内存，或降低上限优雅拒绝 |
| CORS 报错（仅直连调试时） | `CORS_ORIGINS` 未含来源 | 加域名；生产走 Next 代理时通常不会触发 |
| 解析 403 / 412 | B站风控、Cookie/UA 变化 | 升级 yt-dlp（重建镜像）；必要时配置 Cookie 兜底（预留能力） |

---

## 10. 成本与配额（提示）

- FC 计费维度：vCPU·秒 + GB·秒 + 公网流量；`instanceConcurrency` 提高会提升单实例利用率，但不会免除实例规格成本。
- Redis、ACR 存储/流量单独计费；ACR 个人版有拉取限流，生产建议企业版。
- 冷启动与镜像拉取会计入请求延迟，不影响计费口径，但影响体验。
- 具体单价与免费额度以阿里云官网当期计费文档为准，本文不写死价格。

---

## 11. 与当前 CI 的关系

`.github/workflows/ci.yml` 现在只有三个 job：`lint & build`、`backend tests`、`e2e locked features`。**CI 不构建、不推送镜像**，因此本文的 build/push 步骤需要手动执行（或在后续任务里加一个带 ACR 凭据的 `docker buildx build --push` job）。

本次 S1-E 顺带修复了一个 CI 假绿：`backend/tests/test_audio_memory.py:135` 的 FFmpeg 管道用例用 `skipif(not check_ffmpeg())` 守卫，而 GitHub `ubuntu-latest` 默认没有 ffmpeg，导致该用例在 CI 中被**静默跳过**（`main` 上实测 `130 passed, 1 skipped`）。现已在 backend job 中安装 ffmpeg 并加前置断言，防止再次漏跑。

---

## 12. 参考

- [阿里云函数计算 FC 3.0 文档](https://help.aliyun.com/zh/functioncompute/)
- [容器镜像服务 ACR 文档](https://help.aliyun.com/zh/acr/)
- [Serverless Devs fc3 组件](https://docs.serverless-devs.com/user-guide/aliyun/#fc3)
- [backend/Dockerfile](../backend/Dockerfile) · [backend/README.md](../backend/README.md)
- [后端环境变量示例](../backend/.env.example) · [前端部署说明](../README.md#部署)
