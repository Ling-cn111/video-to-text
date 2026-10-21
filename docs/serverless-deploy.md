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

## 5. FC 配置：`s.yaml` 示例

以下示例基于 Serverless Devs **fc3** 组件（`edition: 3.0.0`），字段已对照 `devsapp/fc3` 的 `ICustomContainerConfig` 定义。示例文件建议放在仓库外或 `deploy/`（本地使用，不入库也可）。

```yaml
edition: 3.0.0
name: video-to-text-fc
access: default

vars:
  region: cn-hangzhou
  acr: registry.cn-hangzhou.aliyuncs.com
  namespace: your-namespace
  repo: video-to-text-backend
  tag: v0.1.0

resources:
  backend:
    component: fc3
    props:
      region: ${vars.region}
      functionName: video-to-text-backend
      description: video-to-text 云端 ASR 后端（S1-E）
      runtime: custom-container

      customContainerConfig:
        image: ${vars.acr}/${vars.namespace}/${vars.repo}:${vars.tag}
        # 镜像默认 CMD 是 uvicorn --port 8000；这里覆盖为 FC 常用的 9000。
        # 注意：entrypoint 对应镜像 ENTRYPOINT，command 对应镜像 CMD（参数）。
        entrypoint:
          - uvicorn
        command:
          - app.main:app
          - --host
          - 0.0.0.0
          - --port
          - "9000"
        # 必须与容器实际监听端口一致，FC 靠它做健康检查与流量转发
        port: 9000
        healthCheckConfig:
          httpGetUrl: /api/health
          initialDelaySeconds: 10
          periodSeconds: 30
          timeoutSeconds: 3
          failureThreshold: 3
          successThreshold: 1

      # 1 vCPU / 2GB：100MB 音频全程在内存里流转（S1-B），512MB 容易 OOM
      cpu: 1
      memorySize: 2048
      # 秒回路径只需几秒；/api/summarize 是同步调用，30-70s，必须留足
      timeout: 120
      # 允许单实例处理多个并发请求，减少冷启动；不能解决后台任务被冻结的问题
      instanceConcurrency: 10
      # 需要访问 bilibili / 云端 ASR / DeepSeek 等公网服务
      internetAccess: true
      # 使用 Redis 内网地址时必须配置（示例值请替换）
      # vpcConfig:
      #   vpcId: vpc-xxxx
      #   vSwitchIds:
      #     - vsw-xxxx
      #   securityGroupId: sg-xxxx

      environmentVariables:
        # ---- 必填 ----
        TASK_STORE: redis
        REDIS_URL: ${env(REDIS_URL)}
        CLOUD_ASR_API_KEY: ${env(CLOUD_ASR_API_KEY)}
        DEEPSEEK_API_KEY: ${env(DEEPSEEK_API_KEY)}
        # ---- 强烈建议 ----
        ASR_ENGINE: cloud
        ASR_ENGINE_FALLBACK: ""      # 镜像无 faster-whisper，留空禁用本地降级
        LLM_PROVIDER: deepseek
        CORS_ORIGINS: https://your-frontend.vercel.app
        # ---- 可选 ----
        CLOUD_ASR_BASE_URL: https://api.siliconflow.cn/v1
        CLOUD_ASR_MODEL: XingChenAGI/XingChenASR-V3.2-Ultra
        RATE_LIMIT_PER_MINUTE: "10"  # 0 = 关闭；配置 REDIS_URL 后为多实例共享配额
        MAX_AUDIO_SIZE_MB: "100"
        EXTRACT_TIMEOUT_SECONDS: "30"

      logConfig: auto

      triggers:
        - triggerName: httpTrigger
          triggerType: http
          triggerConfig:
            authType: anonymous
            methods:
              - GET
              - POST
```

### 5.1 关于 9000 端口

需求默认用 FC 常用的 **9000**。镜像本身监听 8000，因此 s.yaml 通过 `entrypoint` / `command` 覆盖启动参数。两种等价做法：

1. **覆盖启动命令 + `port: 9000`**（上面的示例）；
2. **不覆盖命令，直接把 `port` 改成 `8000`**，与镜像 `CMD` 完全一致，少一层变量：

   ```yaml
   customContainerConfig:
     image: ${vars.acr}/${vars.namespace}/${vars.repo}:${vars.tag}
     port: 8000
   ```

无论选哪种，**`port` 必须等于 uvicorn 实际监听端口**。端口不一致的典型表现是函数部署成功但请求 502/超时、健康检查一直失败。若你的 FC 控制台把「启动命令」拆成 `entrypoint` / `command` 两个框，语义与 Docker 一致（entrypoint = 可执行文件，command = 参数）；不确定时优先用做法 2。

### 5.2 部署与查看

```bash
# 安装 Serverless Devs（需 Node.js）
npm i -g @serverless-devs/s

# 配置阿里云 AccessKey（建议 RAM 子账号，授予 FC + ACR 相关权限）
s config add

# 部署（-t 指定 s.yaml）
s deploy -t s.yaml

# 查看函数信息与 HTTP 触发器 URL（形如 https://video-to-text-backend.<region>.fcapp.run）
s info -t s.yaml
```

控制台等价路径：函数计算 3.0 → 创建函数 → **自定义容器** → 选择 ACR 镜像 → 监听端口填 9000（或 8000）→ 配置环境变量 / VPC / 实例规格 → 创建 HTTP 触发器。

> 若 `s deploy` 提示缺少代码目录，可补 `code: ./`（自定义容器只使用镜像，代码目录不会被打进镜像，但组件校验可能需要该字段）。

---

## 6. 环境变量清单

### 6.1 必填（4 项）

| 变量 | 示例 | 说明 |
| --- | --- | --- |
| `TASK_STORE` | `redis` | 必须。`memory` 会写实例内 SQLite（`/app/data`），实例回收即丢，且多实例互不可见 |
| `REDIS_URL` | `redis://:password@r-xxxx.redis.rds.aliyuncs.com:6379/0` | 必须。任务状态 + 限流共享计数。未配置会**静默降级** memory 并打警告 |
| `CLOUD_ASR_API_KEY` | `sk-xxxx` | 必须。未配置时提交云端转写直接报 `CLOUD_NOT_CONFIGURED` |
| `DEEPSEEK_API_KEY` | `sk-xxxx` | 必须（总结）。未配置时 `/api/summarize` 报 `SUMMARIZE_NOT_CONFIGURED` |

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
| `ENQUEUE_MODE` | `inline` | 任务分发模式。`inline` = 同进程 BackgroundTasks；`fc`/`mns`/`redis` 为 P3/P4 实现位，当前显式报错 |
| `CLOUD_ASR_BASE_URL` / `CLOUD_ASR_MODEL` | 见 `.env.example` | 换服务商/模型时覆盖 |

`TASK_DB_PATH`、`WHISPER_*`、`ENABLE_VOCAL_SEPARATION`、`BILI_COOKIE` 在 FC 上都不需要（`BILI_COOKIE` 为预留能力，当前未实现）。

### 6.3 密钥管理：建议走 KMS

**不要把 Key 写进 s.yaml 提交到 git。** 分层建议：

1. **部署机侧（当前即可用）**：s.yaml 用 `${env(REDIS_URL)}` / `${env(CLOUD_ASR_API_KEY)}` 引用部署机环境变量，由 CI 或本地 shell 注入；仓库里只保留占位符。这是本文示例采用的方式。
2. **KMS 凭据管家（生产推荐）**：在 KMS / 凭据管家创建凭据，函数执行角色只授予读取该凭据的权限（最小权限），部署流水线从 KMS 取值后注入 FC 环境变量。这样密钥不落在仓库、不落在开发者机器上。
3. **运行时拉取**：函数启动时用阿里云 SDK 调 `GetSecretValue` 再写入 `os.environ`。**本仓库当前没有内置 KMS 客户端**，这属于 S2 的应用层改造；在改造前，请用方案 1/2，并接受「FC 控制台里能看到明文环境变量」这一事实——用 RAM 权限和操作审计兜底。

补充：环境变量在 FC 控制台/API 中可被有权限的 RAM 用户读取，务必限制 `fc:UpdateFunction` / `fc:GetFunction` 的授权范围，并开启操作审计。KMS 的具体字段与授权策略以当前 FC / KMS 官方文档为准（本仓库未做端到端验证）。

---

## 7. 部署后验收

```bash
FC_URL=https://video-to-text-backend.cn-hangzhou.fcapp.run

# 1) 存活
curl -s $FC_URL/api/health              # {"status":"ok"}

# 2) Key 是否注入成功（只返回布尔，不含 Key）
curl -s $FC_URL/api/capabilities        # {"cloudAsrConfigured":true,"summarizeConfigured":true}

# 3) 解析（同步，1-2 秒）
curl -s -X POST $FC_URL/api/parse \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.bilibili.com/video/BV1xxxxxxxxx"}'

# 4) 字幕快路径（有字幕的视频；返回 taskId 后轮询）
curl -s -X POST $FC_URL/api/transcribe \
  -H 'Content-Type: application/json' \
  -d '{"videoId":"BV1xxxxxxxxx","engine":"cloud"}'

curl -s $FC_URL/api/transcribe/<taskId>
```

判读：

- 第 4 步返回 `{"status":"processing",...}` 是正常的（接口本身就是异步创建）。
- 轮询几次后变 `completed` 且 `transcriptSource` 为 `subtitle_cc` / `subtitle_ai` → 秒回路径打通。
- 一直 `processing` → 后台任务被冻结，属预期限制（§8），不是配置错误。

前端切换：

```bash
# Vercel 环境变量
BACKEND_ORIGIN=https://video-to-text-backend.cn-hangzhou.fcapp.run
NEXT_PUBLIC_USE_MOCK=false        # 构建期常量，改完必须重新构建
```

然后在 Vercel 重新部署，打开首页确认后端可达横幅消失、解析/字幕流程正常。

日志：FC 控制台 → 函数 → 日志（`logConfig: auto` 会写入 SLS），或 `s logs -t s.yaml`。排查时优先看容器启动日志（uvicorn 是否 listen 成功）和 `redis` 降级警告。

---

## 8. 已知限制（S2 预埋）

### 8.1 BackgroundTasks 在 Serverless 下不可靠（最重要）

- **现状**：`POST /api/transcribe` 把 `run_transcription_pipeline` 交给 FastAPI `BackgroundTasks`，在 HTTP 响应返回后执行。
- **FC 行为**：请求结束且实例无活跃请求后，实例会被冻结/回收，后台协程/线程不再获得 CPU 时间。
- **后果**：任务长期停在 `processing`（`stage` 可能停在 `parse_link` / `extract_audio` / `asr`），前端无限轮询；由于 Redis 任务记录**没有 TTL**，这些记录会一直堆积。
- **当前可用的范围**：只把 FC 当作「解析 + 总结 + 秒回字幕」的服务。字幕快路径 5-7 秒，热实例上通常能在冻结前跑完，但这属于 **best effort，不是 SLA**。
- **不可用的范围**：云端 ASR（150 秒音频 19-43 秒）、本地 ASR、任何长任务。

### 8.2 其他限制

| 限制 | 影响 | 规避/说明 |
| --- | --- | --- |
| 仅支持 amd64 | arm64 镜像启动即崩 | 构建时固定 `--platform linux/amd64` |
| 无持久磁盘 | SQLite / 本地文件不跨实例、不跨重启 | 必须 `TASK_STORE=redis`；只信 `/tmp` 且即用即删（S1-B 已去磁盘化） |
| 多实例 | 内存态任务/限流不共享 | 同上，任务与限流都走 Redis |
| 内存上限 | 音频全程在内存（默认上限 100MB） | `memorySize` ≥ 2048MB；按需调低 `MAX_AUDIO_SIZE_MB` |
| 函数超时 | 同步总结 30-70 秒 | `timeout` ≥ 120 秒，否则网关 504 |
| 冷启动 + 406MB 镜像 | 首个请求慢 | ACR 同地域 / 镜像加速；必要时预留实例 |
| 静态 FFmpeg 为 GPLv3 | 对外分发镜像需遵守 GPL | 保留许可声明；如不可接受，改用 apt 版 FFmpeg（镜像约 +400MB） |
| 无本地 ASR | `ASR_ENGINE=local` 不可用 | 固定 `cloud`，`ASR_ENGINE_FALLBACK` 留空 |
| B站风控 / yt-dlp 失效 | 解析或字幕失败 | 定期重建镜像升级 `yt-dlp`；关注 `EXTRACT_TIMEOUT_SECONDS` |
| Redis 任务无 TTL | 任务记录无限增长 | **S2-P0 已修**：`TASK_TTL_SECONDS`（默认 7 天，滑动续期）；0 = 永久保留 |
| 冷启动即改写任务状态 | 多实例下误杀在途任务 | **S2-P0 已修**：只把「陈旧」（`TASK_STALE_SECONDS` 内无心跳）的 `processing` 判为 `interrupted`；Worker 侧设 `TASK_INIT_MODE=none` |

### 8.3 S2 改造方向：独立函数触发

目标：让异步 ASR 不再依赖「响应后的后台任务」。

1. **拆成两个函数**
   - `api` 函数（本文镜像）：只处理解析、总结、字幕快路径；`POST /api/transcribe` 改为**写任务 + 投递消息**后立即返回。
   - `worker` 函数（同一镜像，另加入口/命令）：消费队列，执行下载 + ASR，写回 Redis 任务状态。
2. **触发方式（二选一）**
   - FC **异步调用**（`InvokeFunction` 异步模式）：`api` 投递事件，`worker` 独立执行，不受原请求生命周期影响；
   - **MNS / RocketMQ 队列触发**：天然带重试与削峰，配合死信队列。
3. **状态与幂等**：任务状态继续放 Redis（`TASK_STORE=redis` 已具备），worker 按 `taskId` 幂等更新；补 TTL 与失败重试上限。
   > **S2-P0 已完成**：`TASK_TTL_SECONDS`（默认 7 天滑动续期）、`TASK_INIT_MODE=none`（Worker 启动不改写状态）、`attempts` 字段、进度心跳（`TASK_HEARTBEAT_SECONDS`）与写入节流、同步桥接连接泄漏修复。
   > **S2-P1 已完成**：`services/dispatcher.py` + `TranscribeJob`（完整作业体，可 `to_dict()` 作 FC 事件 payload）+ `ENQUEUE_MODE=inline|fc|mns|redis`（默认 inline，行为不变；未实现模式显式报错）。
   > 仍待 P2/P4：Worker 幂等领取（`SET NX EX`）、超时收割（reaper）、`TASK_TIMEOUT` 错误码。
4. **前端零改动**：仍是 `POST /api/transcribe` + `GET /api/transcribe/{taskId}` 轮询，契约不变。
5. **超时/规格**：worker 函数单独配置更长超时、更大内存；`api` 函数保持小规格低成本。

在此之前，请不要把 FC 版本用于「无字幕视频」的生产流量。

---

## 9. 故障排查

| 现象 | 常见原因 | 处理 |
| --- | --- | --- |
| 启动失败：`exec format error` | 镜像不是 amd64 | 重新用 `--platform linux/amd64` 构建并校验 `docker image inspect` |
| 拉取镜像失败 / `ImagePullBackOff` | ACR 地址错、无权限、跨地域、VPC 未打通 | 检查镜像地址与 tag；配置免密拉取或 `registryConfig`；企业版 VPC 地址需配 `vpcConfig` |
| 请求 502 / 健康检查失败 | 端口不一致；uvicorn 未监听 `0.0.0.0`；启动即崩 | 对齐 `port` 与 `--port`；看启动日志；本地 `docker run` 复现 |
| 请求超时 / 504 | `timeout` 太小（总结 30-70s） | 调到 120s 以上 |
| 任务一直 `processing` | BackgroundTasks 被冻结 | 预期限制，见 §8；只跑秒回路径 |
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
