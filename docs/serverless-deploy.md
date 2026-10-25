# 阿里云函数计算（FC）部署指南

> 适用镜像：`backend/Dockerfile`（PR #43 / S1-D，云端 ASR 专用）　作者：S1-E　日期：2026-10-21
> 最近更新：S2-Deploy（VPC / 函数角色 / 控制台全链路验证，2026-10-22）
>
> 关联文档：[backend/README.md § 容器化部署](../backend/README.md#容器化部署s1-d) · [ROADMAP](ROADMAP.md) · [asr-benchmark](asr-benchmark.md)

## 0. 结论先行

**S2-Deploy 之后，这份模板在云上可直接跑通「API → Worker → Redis」全链路**（P3 打通异步分发，P4 补上崩溃兜底，Deploy 补齐网络与权限）。

| 能力 | FC 上是否可用 | 说明 |
| --- | --- | --- |
| `POST /api/parse`（view API / yt-dlp 解析） | ✅ | 同步接口，1-2 秒返回。B站走 `view` API，不碰网页 |
| `POST /api/summarize`（DeepSeek 总结） | ✅ | 同步接口，30-70 秒，需把函数超时调到 ≥ 90 秒 |
| 字幕快路径转写（B站 CC / AI 字幕，5-7 秒） | ✅ | 走 worker 函数异步执行（P3） |
| 云端 ASR 转写（19-43 秒 / 150 秒音频） | ✅ | 走 worker 函数异步执行（P3），`timeout: 1800` |
| 任务存储（多实例共享） | ✅ | 需 `vpcConfig` + 内网 Redis；未配会**静默降级 memory**（§4.2） |
| 崩溃恢复（OOM / 超时被杀） | ✅ | 定时触发器每 5 分钟收割僵尸任务（P4，见 §7.3.1） |
| 本地 ASR（faster-whisper） | ❌ | 镜像不含本地重型依赖（S1-D 设计），`ASR_ENGINE=cloud` |

历史背景：S1 时代 `POST /api/transcribe` 用 FastAPI `BackgroundTasks` 在**响应返回之后**跑转写，FC 在无活跃请求时会冻结实例，任务永远停在 `processing`。S2 把它拆成「api 函数分发 + worker 函数执行」，并在 P4 补上 reaper 兜底（见 §8）。

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
| 镜像架构 | **linux/amd64** | FC 不支持 arm64；云端构建天然满足，详见 §3 |
| 镜像仓库 | 阿里云 ACR（个人版或企业版） | **必须与 FC 同地域**；§4.1 |
| 本地 Docker | **非必需** | 用 §3.1 控制台代码源构建时完全不需要 |
| 任务存储 | `TASK_STORE=redis` + `REDIS_URL` | 多实例必需；SQLite 在 FC 上不可用 |
| Redis | 阿里云 Redis（或兼容服务） | 放在 VPC 内网，FC 需配 `vpcConfig`（§4.2） |
| VPC / 交换机 / 安全组 | 与 Redis 同 VPC；交换机在 FC 支持的可用区 | 见 §4.2，三个 ID 通过环境变量注入 |
| FC 运行时 | `custom-container` | FC 3.0 + Serverless Devs `fc3` 组件 |
| 前端 | Vercel（或任意 Next.js 托管） | `BACKEND_ORIGIN` 指向 FC 触发器地址 |
| 账号权限 | RAM 子账号：FC 部署 + ACR 推送/拉取（配 `FC_ROLE_ARN` 时另需 `ram:PassRole`） | 不要用主账号 AccessKey |

---

## 3. 构建镜像（云端构建，本地不需要 Docker）

构建上下文是 `backend/`，不是仓库根目录。

### 3.0 先搞清一件事：`s deploy` 会自己 push 镜像

这是本项目最容易卡住的地方，先说结论：

**fc3 组件遇到 `custom-container` 函数时，`s deploy` 默认会在本机执行**

```bash
echo <临时token> | docker login <registry> --username <临时用户> --password-stdin
docker push <image>
```

（源码位置：组件 `subCommands/deploy/impl/function.ts` → `resources/acr/index.ts` 的 `pushAcr()`。）

也就是说：**只要 `s.yaml` 里的镜像地址是 ACR 域名，`s deploy` 就一定会在本地跑
docker push。** 本地 Docker 登不上 ACR 时，这一步必然失败——和你的代码、
`s.yaml` 内容都无关，报错只会说 push 失败。

所以本地 Docker 不可用时，**必须加 `--skip-push`**：

```bash
s deploy -y -t s.yaml --skip-push
```

`--skip-push` 会同时跳过「ACR 里镜像是否存在的检查」和「docker push」，
s deploy 只负责把镜像地址写进函数配置。前提是：**镜像必须已经在 ACR 里**。
镜像怎么进去见 §3.1；如果本地 Docker 可用，也可以走 §3.3。

> `deploy.ps1` 已经默认带 `--skip-push`。只有确实想在本地 build + push 时，
> 才加 `-PushImage` 开关。

### 3.1 方式 A：ACR 控制台「代码源构建」（推荐，完全不碰本地 Docker）

个人版 ACR 自带构建功能：把 GitHub 仓库绑上去，让阿里云那边构建并推进仓库。
全程不需要本地 Docker，也不需要本地能登录 ACR。这也是最稳的路子——构建发生在
阿里云机房，天然是 linux/amd64。

**前置**：仓库已推到 GitHub（本项目为 `Ling-cn111/video-to-text`）。

**Step 1 绑定代码源**

1. 打开 [容器镜像服务控制台](https://cr.console.aliyun.com)，顶部地域选 **华东1（杭州）**
2. 左侧「实例列表」→ 点开**个人版**实例
3. 左侧「仓库管理」→「代码源」→ GitHub 那一行点「绑定账号」
4. 按提示跳转 GitHub 授权（只需一次）

**Step 2 创建镜像仓库并配构建规则**

1. 「仓库管理」→「镜像仓库」→「创建镜像仓库」
2. 命名空间选/建 `vtt-space1`，仓库名 `vtt-backend`，仓库类型选**私有**
3. 「下一步」进入**代码源**配置：
   - 代码源：GitHub → 选中 `video-to-text` 仓库
   - 构建设置：
     - **代码变更时自动构建镜像**：⚠️ **建议关闭**。开启后每次 `git push main`
       都会用云端构建机重新构建并覆盖 `:v1` 标签，**静默覆盖掉你本地
       `docker push` 的镜像**——表现为「明明推了新镜像、`s deploy` 也成功，
       但函数行为没变」。排查时用
       `docker buildx imagetools inspect <image>:<tag>` 看 registry 上的
       digest，与 `s info` 里的 `resolvedImageUri` 对比即可确认。
       需要自动构建就换一个独立标签（如 `:ci`），别和手工推送共用 `:v1`。
     - **海外机器构建**：**先保持关闭**；如果日志里出现
       `dial tcp ...:443: i/o timeout` / `failed to resolve source metadata for
       docker.io/...`（拉不到 Docker Hub 基础镜像），就**打开它**再构建一次。
       国内构建机没有 Docker Hub 出海网络，会在第一条 `FROM` 就超时失败，
       与你的 Dockerfile 无关。反过来，海外构建下载 `johnvansickle.com` 的
       ffmpeg 和推回杭州会慢一些，但能跑通，比直接失败好。
     - **不使用缓存**：关闭（开了每次都要重下 ffmpeg，慢很多）
4. 「创建镜像仓库」

**Step 3 构建规则（关键，填错就构不出来）**

仓库详情页 → 左侧「构建」→「添加规则」：

| 字段 | 填什么 | 为什么 |
| --- | --- | --- |
| 类型 | `Branch` | 跟分支走 |
| Branch/Tag | `main` | 你的主分支 |
| **构建上下文目录** | `backend` | ⚠️ **最容易填错的一格** |
| Dockerfile 文件名 | `Dockerfile` | 位于 `backend/Dockerfile` |
| 镜像版本 | `v1` | 必须与 `s.yaml` 的 `vars.tag` 一致 |

> **为什么是 `backend`**：`backend/Dockerfile` 里写的是
> `COPY app ./app`、`COPY requirements-cloud.txt ./`，这些路径都相对
> `backend/`。上下文目录填成根目录 `./` 会找不到 `app/`，构建报
> `COPY failed: no source files were specified`。

**Step 4 立即构建**

规则那一行点「立即构建」，然后点「日志」看进度。

个人版构建**超时上限 30 分钟**，本项目首次构建（下 ffmpeg ≈78MB + 装 Python
依赖）大约 5–10 分钟，够用。如果超时：开了「海外机器构建」的把它关掉试试，
没开的看是否是卡在拉基础镜像（见 Step 2 的说明）。

构建成功后：「镜像版本」页能看到 `v1`。

**Step 5 部署**

```bash
s deploy -y -t s.yaml --skip-push
```

镜像已经在 ACR 里，`--skip-push` 让 s deploy 不去碰本地 Docker。

### 3.2 ⚠️ 不要用 `s build --cloud-build-config`（实测结论）

fc3 v3 确实提供了 `--cloud-build-config`，官方文档也写着「云端构建」。
但**它不读你的 Dockerfile**，本项目用它只会得到一个大而全、且跑不起来的镜像。

实测（2026-10，fc3 组件 v3 + docker-image-builder）：

```
setup task started        setupScript=/code/cloud-build-setup.sh
docker-build-push task started
开始打包并推送镜像          image=registry.cn-hangzhou.aliyuncs.com/vtt-space/video-to-text:v1
pushing image ...
```

从 `docker-build-push` 到 `pushing image` 只用了 **1.3 秒**。多阶段 Dockerfile 构建
（拉 `python:3.12-slim` + 下 78MB 静态 ffmpeg + pip 装依赖）不可能这么快。

在远端 worker 里跑探针，结果很直观：

| 探测项 | 实际结果 |
| --- | --- |
| `which docker` | 无（`no docker in PATH`） |
| `/var/run/docker.sock` | 不存在 |
| `which buildctl kaniko` | 无（只有 `buildah`） |
| `which ffmpeg` | 无 |
| 基础系统 | `Ubuntu 24.04.4 LTS` |

它的真实语义是：用 `baseContainerConfig.image`（一个叫
`docker-image-builder-worker` 的 Ubuntu 24.04 构建镜像）起一个容器 → 把代码解到
`/code` → 跑 `setup.sh` → **把整个容器文件系统打成镜像推走**。

所以产出物是「Ubuntu 24.04 构建机 + 你的代码在 `/code`」：没有 `/app`、
没有 `/opt/venv`、没有 ffmpeg，`s.yaml` 里 `entrypoint: [uvicorn]` 也找不到可执行文件。

要让这条路能用，就得把 `backend/Dockerfile` 的全部内容改写成一个 `setup.sh`
（apt 装 ffmpeg、建 venv、装依赖、把代码放到 `/app`），等于维护两套会漂移的构建逻辑，
而且镜像会从 406MB 涨到 GB 级、还会带上 Go/Node 等构建工具链。**不划算，别走这条路。**

> 结论：本地没有 Docker 时，用 **§3.1 的 ACR 控制台代码源构建**。它是真正
> `docker build`，读的就是 `backend/Dockerfile`。

### 3.3 方式 B：本地 Docker（本地能登录 ACR 时才用）

本地 Docker 正常时最直接，也方便调试镜像本身。

```bash
cd backend

# 确认 buildx 可用（Docker Desktop 默认自带）
docker buildx version

# 构建（务必指定 amd64，FC 不支持 arm64）
docker buildx build --platform linux/amd64 \
  -t crpi-z9t50bxmh0sf6wux.cn-hangzhou.personal.cr.aliyuncs.com/vtt-space1/vtt-backend:v1 --load .
```

**必须校验架构**，否则会得到一个能 push、但在 FC 上起不来的镜像：

```bash
docker image inspect --format '{{.Os}}/{{.Architecture}}' \
  crpi-z9t50bxmh0sf6wux.cn-hangzhou.personal.cr.aliyuncs.com/vtt-space1/vtt-backend:v1
# 期望输出：linux/amd64
```

常见坑：

- Apple Silicon 上不加 `--platform` 会构建出 `linux/arm64`，FC 启动时报
  `exec format error`。
- 老版本 Docker 的经典构建器会**忽略** `--platform` 并打印警告，务必用 `buildx`。
- 镜像实测约 **406MB**。首次冷启动受镜像拉取时间影响。

推送有两种选择：

```bash
# 选择 1：手动 push，然后部署时带 --skip-push
docker login --username=<阿里云账号> crpi-z9t50bxmh0sf6wux.cn-hangzhou.personal.cr.aliyuncs.com
docker push crpi-z9t50bxmh0sf6wux.cn-hangzhou.personal.cr.aliyuncs.com/vtt-space1/vtt-backend:v1
s deploy -y -t s.yaml --skip-push

# 选择 2：交给 s deploy 自动 push（不要加 --skip-push）
s deploy -y -t s.yaml
```

本地自检（镜像默认监听 8000，`docker run` 里映射到 8000）：

```bash
docker run --rm -p 8000:8000 \
  -e ASR_ENGINE=cloud \
  -e CLOUD_ASR_API_KEY=sk-xxxx \
  -e ASR_ENGINE_FALLBACK= \
  -e TASK_STORE=redis \
  -e REDIS_URL=redis://host:6379/0 \
  -e DEEPSEEK_API_KEY=sk-xxxx \
  crpi-z9t50bxmh0sf6wux.cn-hangzhou.personal.cr.aliyuncs.com/vtt-space1/vtt-backend:v1

curl -s localhost:8000/api/health        # {"status":"ok"}
curl -s localhost:8000/api/capabilities  # {"cloudAsrConfigured":true,"summarizeConfigured":true}
```

### 3.4 三种方式怎么选

| 场景 | 用哪种 | 部署命令 |
| --- | --- | --- |
| **本地 Docker 登不上 ACR（本项目当前情况）** | **方式 A：ACR 控制台代码源构建** | `s deploy -y -t s.yaml --skip-push` |
| 本地 Docker 正常，要调镜像 | 方式 B：本地 buildx | `s deploy -y -t s.yaml` |
| 命令行一键出镜像、不动 GitHub | ❌ 无可用方式（见 §3.2） | — |

---

## 4. 云资源准备（ACR / VPC / 函数角色）

部署前需要备好三类资源。§4.1 是镜像，§4.2 是「能连上 Redis」的前提，§4.3 是可选的函数角色。

### 4.1 ACR 仓库与镜像地址

先在 **容器镜像服务控制台** 创建命名空间与镜像仓库（个人版免费；生产建议企业版，支持 VPC 域名与更稳定的拉取带宽）。**镜像怎么构建推送，见 §3**——本地登不上 ACR 时用 §3.1，不需要本地 Docker。

个人版仓库的**完整地址**形如（本项目的实际值）：

```
crpi-z9t50bxmh0sf6wux.cn-hangzhou.personal.cr.aliyuncs.com/vtt-space1/vtt-backend:v1
└────────────────── 域名 ──────────────────────────┘ └── ns ──┘ └──── repo ────┘└tag┘
```

**`s.yaml` 里只填域名部分**（`vars.acr`），namespace/repo/tag 由 `vars.image` 自己拼：

```yaml
vars:
  acr: crpi-z9t50bxmh0sf6wux.cn-hangzhou.personal.cr.aliyuncs.com   # ← 只有域名
  namespace: vtt-space1
  repo: vtt-backend
  tag: v1
  image: ${vars.acr}/${vars.namespace}/${vars.repo}:${vars.tag}
```

> ⚠️ 把完整镜像地址填进 `vars.acr` 会拼出
> `crpi-.../vtt-space1/vtt-backend:v1/vtt-space1/vtt-backend:v1` 这种废地址，
> 报错却说「镜像不存在」，很难反推。
> `test_acr_var_is_a_registry_host_only` 会拦住这种写法。

**旧域名和新域名是两个不同入口**，这是最容易踩的一脚：在新版个人版实例下填
`registry.cn-hangzhou.aliyuncs.com`，FC 会去那个（你账号里并不存在的）旧入口找仓库，
报 `REPO_NOT_EXIST`，而你的仓库其实好好的。判断方法：ACR 仓库详情页的「公网地址」
域名是什么，`vars.acr` 就必须是什么。

镜像地址按 ACR 版本不同：

| 版本 | 公网地址 | 内网（VPC）地址 |
| --- | --- | --- |
| 个人版（旧） | `registry.<region>.aliyuncs.com/<ns>/<repo>:<tag>` | 同左（旧个人版无独立 VPC 域名） |
| 个人版（新） | `crpi-xxxxxxxx.<region>.personal.cr.aliyuncs.com/<ns>/<repo>:<tag>` | `crpi-xxxxxxxx-vpc.<region>.personal.cr.aliyuncs.com/...` |
| 企业版 | `<instance>-registry.<region>.cr.aliyuncs.com/<ns>/<repo>:<tag>` | `<instance>-registry-vpc.<region>.cr.aliyuncs.com/...` |

建议：

- **同地域是硬要求**：FC 只拉「同账号 + 同地域」的 ACR 镜像。自 **2026-02-01** 起 FC 已**不支持跨地域拉取 ACR 个人版镜像**；新版个人版实例（`crpi-` 开头）更是绑定创建时的地域、不能改。`test_acr_region_matches_function_region` 会校验域名里的地域与 `vars.region` 一致。
- **一个账号只有一个 ACR 个人版实例**，且旧版实例不支持删除重建换地域——所以实例在哪个地域，函数就必须部署在哪个地域。
- FC 支持「ACR 镜像免密拉取」，可避免在 s.yaml 里写镜像仓库密码；若用私有仓库且不免密，需在 `customContainerConfig.registryConfig.authConfig` 配置账号密码（**不要提交明文到 git**）。
- 用**企业版 VPC 域名**（`<instance>-registry-vpc.<region>.cr.aliyuncs.com`）时，FC 拉镜像走的是「函数所在 VPC」，因此必须给函数配 `vpcConfig`（§4.2）；若函数不在该 VPC，则要改用 `customContainerConfig.registryConfig.networkConfig` 单独指定拉取用的 VPC/交换机/安全组。同账号用公网域名 + 免密拉取时两者都不用配。

### 4.2 准备 VPC / 交换机 / 安全组（访问云 Redis 的前提）

**为什么必须配**：云数据库 Redis 的内网地址只在 VPC 内可达。函数不配 `vpcConfig` 时连接会超时，而应用侧会**静默降级到 memory 存储**（只打一条警告）——现象是「每个实例各有一份任务，多实例之间互相看不见」，几乎不可能从业务日志反推到网络配置。

FC 3.0 的默认网络能力是「可访问公网、不可访问 VPC」。本项目需要**两者都要**：

| 需求 | 目标 | 所需配置 |
| --- | --- | --- |
| 读 VPC 内网 Redis | `r-xxxx.redis.rds.aliyuncs.com:6379` | `vpcConfig` 三个字段 |
| 下载 B站音频、调云端 ASR / DeepSeek | 公网 | `internetAccess: true` |

官方明确支持「允许访问 VPC = 是 + 允许函数访问公网 = 是」这一组合（非 GPU 函数还支持 PrivateZone 私有域名解析）。**只配 VPC 不开 `internetAccess` 会让下载与 ASR 全部失败**，而 Redis 反而正常——排查方向会被完全带偏。

准备步骤：

1. **确认 Redis 所在的 VPC 与交换机**：云数据库 Redis 控制台 → 实例详情 → 「网络信息」里能看到 VPC ID 与交换机 ID。
2. **确认交换机可用区是 FC 支持的**：FC 只在部分可用区有资源。`cn-hangzhou` 支持 `h / i / j / k / f / g`；其他地域的列表见[函数计算支持的可用区](https://help.aliyun.com/zh/functioncompute/configure-network-settings)。交换机落在不支持的可用区会报
   `InvalidArgument: vSwitch is in unsupported zone`。
   > 如果 Redis 的交换机不在支持列表里，**不要重建 Redis**：在同一 VPC 内、FC 支持可用区下新建一个交换机，把它填进 `vpcConfig` 即可——同一 VPC 内不同交换机私网互通。
3. **准备安全组**（挂在函数 ENI 上，管的是**函数出方向**）：
   - 出方向放行 `6379`（访问 Redis）与 `ICMP`（FC 会用它探测 VPC 连通性）；
   - 入方向无需为 Redis 开口（那是 Redis 侧白名单的事）。
4. **配 Redis 白名单**（管的是**谁能连进 Redis**）：把**函数所在交换机的网段**加入 Redis 白名单（例如 `192.168.1.0/24`）。白名单没配的典型表现同样是「连接超时」，且日志里看不出原因。
5. **建议给两个交换机**：`vSwitchIds` 是列表，官方建议至少两个可用区，单可用区故障或 IP 不足时函数能调度到另一个子网。本仓库模板给了一个，按需追加。

```bash
# 部署机环境变量（三个 ID 都是账号内资源，不进仓库）
export FC_VPC_ID=vpc-bp1xxxxxxxxxxxx
export FC_VSWITCH_ID=vsw-bp1xxxxxxxxxxxx
export FC_SECURITY_GROUP_ID=sg-bp1xxxxxxxxxxxx
```

> **代价**：官方明确「使用 VPC 功能会降低函数计算的冷启动效率」，建议非必要勿配。本项目因为必须连内网 Redis，属于必要场景。若你暂时用公网 Redis（有安全风险且无 TLS），把 `s.yaml` 里 api / worker 两处 `vpcConfig` 删掉即可。

### 4.3 函数角色（`role`）：先留空，需要时再建

`role` 字段的语义常被误解，这里说清楚三件事各自由谁负责：

| 要做的事 | 实际由谁负责 | 与 `role` 的关系 |
| --- | --- | --- |
| 拉取 ACR 镜像 | FC **服务关联角色** `AliyunServiceRoleForFC`（含 `cr:PullRepository` 等）；跨账号才需 `registryConfig.authConfig` | 无关 |
| 打通 VPC（创建 ENI） | 同上（含 `ecs:CreateNetworkInterface`、`vpc:DescribeVSwitchAttributes`）+ `vpcConfig` | 无关 |
| 把日志写到 SLS | 同上（`logConfig: auto` 即可） | 无关 |
| **函数代码里访问其他云服务**（OSS / KMS / 表格存储…） | **函数角色**（`role`）：FC 会 `AssumeRole` 拿临时 STS 并注入请求头 | 这才是它的用途 |

所以：**本项目默认不需要配置 `role`**。模板里写成

```yaml
role: ${env('FC_ROLE_ARN', '')}
```

默认渲染成空串 → fc3 使用服务关联角色。首次登录 FC 控制台时系统会引导创建 `AliyunServiceRoleForFC`（若没创建，部署会提示需要授权）。

只有在「函数代码里要读 KMS 凭据」这类场景才需要自定义角色：

1. RAM 控制台 → 角色 → 创建角色 → 可信主体类型选 **云服务** → 信任主体名称选 **函数计算/FC**（等价于信任策略里 `Service` 为 `fc.aliyuncs.com`）。
2. 给角色授予所需的最小权限策略（例如读某个 KMS 凭据）。
3. 把 ARN 写进部署机变量：`export FC_ROLE_ARN=acs:ram::<账号ID>:role/<角色名>`。

> ARN 格式必须是 `acs:ram::<账号ID>:role/<角色名>`。只写角色名时 fc3 会尝试自动补全（`Role.completionArn`），但手写 YAML 时写错格式会被 FC 直接拒绝。
> 填了 `FC_ROLE_ARN` 时，**部署账号**还需要 `ram:PassRole`（条件 `acs:Service = fc.aliyuncs.com`），否则把角色绑到函数上会失败；留空则不需要。
> 用 `vpcConfig` 的显式配置（而非 `auto`）时，部署账号**不需要** VPC / ECS 写权限——创建 ENI 由 FC 服务关联角色完成。`vpcConfig: auto` 才需要 `vpc:CreateVpc` / `vpc:CreateVSwitch` / `ecs:CreateSecurityGroup` 等一串权限，而且它新建的是一个**空 VPC**，里面并没有你的 Redis，所以本项目不选它。

---

## 5. FC 配置：`s.yaml` 双函数模板

仓库根目录的 **`s.yaml`** 就是完整可用的模板（`edition: 3.0.0`，Serverless Devs **fc3** 组件，字段已对照 `devsapp/fc3` 的 `ICustomContainerConfig` / `IAsyncInvokeConfig` schema）。它定义两个函数：

| 函数 | 启动命令 | 职责 | 规格 |
| --- | --- | --- | --- |
| `video-to-text-api` | `uvicorn app.main:app` | 解析 / 总结 / 字幕快路径 / **投递异步作业** | 1C2G / 300s / 并发 10（实例上限 5） |
| `video-to-text-worker` | `uvicorn app.worker_app:app` | 下载 + ASR（长任务） | 2C4G / 1800s / 并发 1 |

两者用**同一个镜像**，只在 `customContainerConfig.command` 上区分 ASGI 应用。

### 5.1 完整模板（可直接复制）

下面是仓库根目录 `s.yaml` 的完整副本（`backend/tests/test_deploy_manifest.py::test_documented_template_matches_the_real_manifest` 会解析比对，防止文档漂移——改 `s.yaml` 记得同步这一段）。**相对 P3 版本新增了两块：每个函数的 `vpcConfig`（访问内网 Redis）与 `role`（函数执行角色，默认留空）。**

<!-- BEGIN:s.yaml -->
```yaml
# 阿里云函数计算（FC 3.0）部署描述：API + Worker 双函数（S2-P3 分发 / S2-Deploy 网络与角色）
#
# 部署：s deploy -t s.yaml
# 查看：s info -t s.yaml      # 记录两个 HTTP 触发器地址
#
# 首次部署是「两段式」，因为 API 函数需要 worker 的触发器地址（WORKER_URL）：
#   1) 先 s deploy 建出两个函数（WORKER_URL 留空不阻断启动，只是分发会失败）
#   2) 从 s info 取 worker 的 https://...fcapp.run 地址，写入部署机环境变量
#      WORKER_URL 后，再 s deploy 一次让 API 函数拿到它
#
# 注意：函数 environmentVariables 里**不能**出现 FC_ 开头的键——阿里云把该前缀
# 保留给系统变量（FC_FUNC_CODE_PATH / FC_HANDLER …），设置即被平台拒绝。
# 部署机变量用 FC_VPC_ID / FC_VSWITCH_ID / FC_SECURITY_GROUP_ID 是安全的，
# 它们只被 ${env(...)} 求值，不会进入容器。
#
# 所有账号相关取值都用 ${env('NAME')} 引用部署机环境变量，仓库里不含明文、也不含
# 账号内的资源 ID（注意必须带引号，写成 ${env(NAME)} 会被当成 JS 变量而报
# NAME is not defined）：
#   $env:REDIS_URL="..."            # PowerShell（见根目录 deploy.ps1）
#   export REDIS_URL=... CLOUD_ASR_API_KEY=... DEEPSEEK_API_KEY=... \
#          WORKER_TOKEN=... WORKER_URL=... CORS_ORIGINS=... \
#          FC_VPC_ID=... FC_VSWITCH_ID=... FC_SECURITY_GROUP_ID=...
# 可选的 FC_ROLE_ARN 留空即使用 FC 服务关联角色（详见 docs/serverless-deploy.md §4.3）。
#
# 设计要点见 docs/serverless-deploy.md §5、docs/s2-async-recon.md §8。

edition: 3.0.0
name: video-to-text-fc
access: default

vars:
  region: cn-hangzhou
  # acr 只填**仓库域名**，不要带 namespace/repo:tag —— 下面的 image 会自己拼。
  # 填成完整镜像地址会拼出 registry/...:v1/vtt-space1/vtt-backend:v1 这种废地址。
  # 取值：ACR 控制台 → 实例列表 → 目标实例 → 仓库管理 → 镜像仓库 → 仓库详情页
  # 顶部的「公网地址」去掉 /<namespace>/<repo> 部分。两种形态都要与 region 同地域：
  #   旧个人版 registry.<region>.aliyuncs.com
  #   新个人版 crpi-xxxxxxxx.<region>.personal.cr.aliyuncs.com   ← 本项目用这个
  # 两者是**不同入口**，填错会报 REPO_NOT_EXIST（FC 去旧域名下找你的仓库，永远找不到）。
  acr: crpi-z9t50bxmh0sf6wux.cn-hangzhou.personal.cr.aliyuncs.com
  namespace: vtt-space1
  repo: vtt-backend
  tag: v1
  image: ${vars.acr}/${vars.namespace}/${vars.repo}:${vars.tag}

resources:
  # ============================================================
  # API 函数：解析链接 / 总结 / 字幕快路径 / 投递异步作业
  # ============================================================
  api:
    component: fc3
    props:
      region: ${vars.region}
      functionName: video-to-text-api
      description: video-to-text API（S2-P3：分发到 worker 函数）
      runtime: custom-container
      # 自定义容器只用镜像：fc3 部署前会主动 unset code（代码目录不会被打包上传），
      # 保留它只是为了将来若切回非容器运行时可复用
      code: ./backend

      customContainerConfig:
        image: ${vars.image}
        # entrypoint = 镜像 ENTRYPOINT（可执行文件），command = 参数（覆盖镜像 CMD）
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

      # 1C2G：解析 + 总结（同步 30-70s）；音频全程在内存流转（S1-B），512MB 易 OOM
      cpu: 1
      memorySize: 2048
      # 同步总结最长 30-70s，长视频（>1 小时）加长文本总结会显著拉长；
      # 300s 留足余量，避免 504 网关超时。异步分发只等 202，不受此限
      # fc3 的 Disk 是必填项（缺了报 Disk is required but not provided），
      # 512MB 是允许的最小值；音频流在内存里，磁盘只放临时文件
      diskSize: 512
      timeout: 300
      # 允许单实例处理多个并发请求，降低冷启动影响
      instanceConcurrency: 10
      # 实例数上限（P2 防刷爆额度）：FC3 没有函数级 maxInstance 字段，
      # 弹性上限走 concurrencyConfig.reservedConcurrency（预置并发/实例数配额）。
      # 5 实例 × instanceConcurrency 10 = 最多 50 并发，挡得住刷量与异常重试风暴。
      concurrencyConfig:
        reservedConcurrency: 5
      # 需访问 worker 的 fcapp.run、bilibili、云端 ASR、DeepSeek
      internetAccess: true
      # ---- VPC 网络（访问云 Redis 内网地址必需）----
      # 云数据库 Redis 的内网地址只在该 VPC 内可达，函数不配 VPC 时连接会超时，
      # 应用侧会「静默降级 memory」并打警告——表现为多实例任务互不可见，很难排查。
      # 三个值都是账号内的资源 ID，必须由部署机环境变量提供（仓库不含账号信息）。
      # 注意：vSwitchId 必须落在 FC 支持的可用区内（cn-hangzhou: h/i/j/k/f/g），
      # 否则部署报 InvalidArgument: vSwitch is in unsupported zone。
      # 若 Redis 走公网地址（不推荐，无 TLS 且暴露公网），删掉 api/worker 两处 vpcConfig。
      vpcConfig:
        vpcId: ${env('FC_VPC_ID')}
        vSwitchIds:
          - ${env('FC_VSWITCH_ID')}
        securityGroupId: ${env('FC_SECURITY_GROUP_ID')}

      # ---- 函数执行角色（RAM）----
      # 留空 = 使用 FC 服务关联角色 AliyunServiceRoleForFC，它已覆盖「写 SLS 日志 +
      # 创建 ENI 打通 VPC + 拉取同账号 ACR 镜像」，够本项目用，推荐先留空。
      # 只有需要「函数代码内访问其他云服务（OSS/KMS/表格存储…）」时才填自定义角色 ARN：
      #   acs:ram::<账号ID>:role/<角色名>
      # 注意：这个字段**不是** ACR 拉取或 VPC 通路的开关——那两件事由 FC 服务关联角色
      # 与 vpcConfig 负责，填错角色反而可能让日志投递失败（详见 docs/serverless-deploy.md §4.3）。
      role: ${env('FC_ROLE_ARN', '')}

      environmentVariables:
        # ---- 存储（必填）----
        TASK_STORE: redis
        REDIS_URL: ${env('REDIS_URL')}
        TASK_TTL_SECONDS: "604800"
        # API 侧负责把「陈旧」processing 任务判为 interrupted
        TASK_INIT_MODE: recover
        TASK_STALE_SECONDS: "600"
        # 排队中任务（started_at 为空、没有心跳）的判定阈值，实际生效取
        # max(TASK_STALE_SECONDS, 本项)。Worker 的 instanceConcurrency 是 1，
        # 派发积压时后面排队的任务可能等十几分钟才有机会开跑，套用 600s 会被
        # init/reaper 误标 interrupted/failed。调大即放宽排队容忍度。
        REAPER_QUEUED_STALE_SECONDS: "1800"
        TASK_HEARTBEAT_SECONDS: "30"
        # ---- 外部服务（必填）----
        CLOUD_ASR_API_KEY: ${env('CLOUD_ASR_API_KEY')}
        DEEPSEEK_API_KEY: ${env('DEEPSEEK_API_KEY')}
        ASR_ENGINE: cloud
        # 镜像不含本地 ASR，留空禁用降级（否则云端失败后会尝试本地并报「未安装」）
        ASR_ENGINE_FALLBACK: ""
        LLM_PROVIDER: deepseek
        # ---- Web ----
        CORS_ORIGINS: ${env('CORS_ORIGINS', 'https://your-frontend.vercel.app')}
        RATE_LIMIT_PER_MINUTE: "10"
        MAX_AUDIO_SIZE_MB: "100"
        EXTRACT_TIMEOUT_SECONDS: "30"
        # ---- 平台风控（B站 412）----
        # 机房出口 IP 是 B站风控重点，缺 Referer/UA 时易报 412。
        # 留空则沿用 yt-dlp 默认值；Cookie 是账号凭证，只经部署机注入。
        BILI_USER_AGENT: ${env('BILI_USER_AGENT', '')}
        BILI_COOKIE: ${env('BILI_COOKIE', '')}
        # ---- S2-P3：异步分发 ----
        ENQUEUE_MODE: fc
        # worker 函数的 HTTP 触发器地址（s info 里取），首次部署可留空
        WORKER_URL: ${env('WORKER_URL', '')}
        WORKER_PATH: /internal/worker/transcribe
        ASYNC_TIMEOUT_SECONDS: "5"
        # 与 worker 函数保持一致，Worker 端点用它校验投递来源
        WORKER_TOKEN: ${env('WORKER_TOKEN', '')}

      logConfig: auto

      triggers:
        - triggerName: httpTrigger
          triggerType: http
          qualifier: LATEST
          triggerConfig:
            # 公网入口，鉴权由应用层（限流 + 业务校验）承担
            authType: anonymous
            # 只放行实际用到的：GET=健康检查/任务查询，POST=解析/转写/总结，
            # OPTIONS=浏览器 CORS 预检
            methods:
              - GET
              - POST
              - OPTIONS

  # ============================================================
  # Worker 函数：下载音频 + ASR（长任务，异步调用）
  # ============================================================
  worker:
    component: fc3
    props:
      region: ${vars.region}
      functionName: video-to-text-worker
      description: video-to-text Worker（S2-P2/P3：异步执行转写）
      runtime: custom-container
      code: ./backend

      customContainerConfig:
        image: ${vars.image}
        # 同一镜像，只换 ASGI 应用
        entrypoint:
          - uvicorn
        command:
          - app.worker_app:app
          - --host
          - 0.0.0.0
          - --port
          - "9000"
        port: 9000
        healthCheckConfig:
          httpGetUrl: /api/health
          initialDelaySeconds: 10
          periodSeconds: 30
          timeoutSeconds: 3
          failureThreshold: 3
          successThreshold: 1

      # 2C4G：100MB 音频 + yt-dlp + ffmpeg 转码 + 云端 ASR，OOM 风险优先于成本
      cpu: 2
      memorySize: 4096
      # 长视频转写；请对照当前地域配额确认上限，超限需在配额中心申请
      # fc3 必填项，512MB 为最小值；16kHz WAV 会落 /tmp，接近 100MB 上限的
      # 超长音频如遇「磁盘写满」，先调大这里再排查
      diskSize: 512
      timeout: 1800
      # 一实例一作业：内存模型最简单，避免多作业并发把内存打满
      instanceConcurrency: 1
      # worker 既要拉 bilibili / 云端 ASR（公网），又要读 Redis（VPC 内网）：
      # 「允许访问 VPC=是 + 允许函数访问公网=是」是 FC 支持的组合，非 GPU 函数还
      # 支持 PrivateZone 私有域名解析。只配 vpcConfig 而不开 internetAccess 会让
      # 下载与 ASR 全部失败。
      internetAccess: true
      # 必须与 api 函数同一套 VPC/交换机/安全组：任务状态写在 Redis，worker 读不到
      # 就只能降级 memory，表现为「任务永远停在 processing」。
      vpcConfig:
        vpcId: ${env('FC_VPC_ID')}
        vSwitchIds:
          - ${env('FC_VSWITCH_ID')}
        securityGroupId: ${env('FC_SECURITY_GROUP_ID')}
      # 同上：留空使用 FC 服务关联角色
      role: ${env('FC_ROLE_ARN', '')}

      environmentVariables:
        TASK_STORE: redis
        REDIS_URL: ${env('REDIS_URL')}
        TASK_TTL_SECONDS: "604800"
        # 关键：Worker 冷启动不得改写其他实例在途任务（代码里也显式传 False，双保险）
        TASK_INIT_MODE: none
        TASK_STALE_SECONDS: "600"
        # 排队中任务（started_at 为空）的判定阈值，与 API 侧保持一致；
        # 实际生效取 max(TASK_STALE_SECONDS, 本项)。见 reaper.py 的两档阈值。
        REAPER_QUEUED_STALE_SECONDS: "1800"
        TASK_HEARTBEAT_SECONDS: "30"
        # 执行锁 TTL，执行期间按心跳续期；崩溃后到期自动释放
        TASK_LOCK_SECONDS: "3600"
        # Reaper 互斥锁 TTL：只需覆盖单次扫描耗时，与 TASK_LOCK_SECONDS 无关
        REAPER_LOCK_SECONDS: "120"
        CLOUD_ASR_API_KEY: ${env('CLOUD_ASR_API_KEY')}
        ASR_ENGINE: cloud
        ASR_ENGINE_FALLBACK: ""
        MAX_AUDIO_SIZE_MB: "100"
        EXTRACT_TIMEOUT_SECONDS: "30"
        # worker 才是真正下载音频、调 yt-dlp 的一方，风控参数必须两边一致
        BILI_USER_AGENT: ${env('BILI_USER_AGENT', '')}
        BILI_COOKIE: ${env('BILI_COOKIE', '')}
        # Worker 不再分发，保持 inline（且不 import dispatcher 的投递路径）
        ENQUEUE_MODE: inline
        WORKER_TOKEN: ${env('WORKER_TOKEN', '')}

      # 异步调用策略：默认 3 次重试与 TASK_TTL_SECONDS（7 天）对齐
      asyncInvokeConfig:
        maxAsyncRetryAttempts: 3
        maxAsyncEventAgeInSeconds: 604800

      logConfig: auto

      # 仅 POST：API 侧以 X-Fc-Invocation-Type: Async 投递，FC 立即回 202
      triggers:
        - triggerName: httpTrigger
          triggerType: http
          qualifier: LATEST
          triggerConfig:
            # 官方文档明确：无需认证时可在业务层校验 Authorization 自行鉴权。
            # 这里由 WORKER_TOKEN（X-Worker-Token / Bearer）兜底。
            # 若要更严：改成 authType: function（签名认证），但会重新引入签名 SDK。
            authType: anonymous
            methods:
              - POST

        # ---- S2-P4：僵尸任务清理（Reaper）----
        # 关键约束：定时触发器是「事件调用」，不能指定 HTTP 路径，FC 固定把 event
        # POST 到容器的 /invoke；它也**不能带自定义请求头**，所以凭据放在 payload 里，
        # 由 worker_app._timer_payload_token 取出后走同一套 _verify_token。
        # P0-2：/invoke 是公网匿名入口，绝不能再靠 triggerTime/triggerName 免鉴权。
        - triggerName: reapTimer
          triggerType: timer
          triggerConfig:
            # FC cron 为 6 段：秒 分 时 日 月 周（最小粒度 1 分钟）
            # 每 5 分钟一轮：远小于 TASK_STALE_SECONDS(600s)，僵尸任务最长滞留约 15 分钟
            cronExpression: "0 */5 * * * *"
            enable: true
            # 凭据放 payload：定时触发器无法带请求头，worker_app._timer_payload_token
            # 会把它取出来交给 _verify_token（P0-2：不再有免鉴权分支）。
            # 用 YAML 双引号标量是因为内层 JSON 也需要双引号；${env(...)} 由 s 在
            # YAML 解析前做文本替换，所以这里必须保持单引号的 env 魔法变量写法。
            payload: "{\"action\":\"reap\",\"token\":\"${env('WORKER_TOKEN', '')}\"}"
```
<!-- END:s.yaml -->

关键结构速览：

| 配置块 | 位置 | 作用 |
| --- | --- | --- |
| `customContainerConfig` | 两函数 | 镜像 + 启动命令 + 监听端口 + 健康检查 |
| `vpcConfig` | 两函数，**必须完全相同** | 访问 VPC 内网 Redis（§4.2） |
| `internetAccess: true` | 两函数 | 与 `vpcConfig` 并存：下载 B站音频、调云端 ASR |
| `role` | 两函数，默认空 | 留空 = FC 服务关联角色（§4.3） |
| `environmentVariables` | 两函数 | 见 §6；账号相关的值全部走 `${env(...)}` |
| `triggers` | api 一个 HTTP；worker 一个 HTTP + 一个 timer | timer 是 P4 的 reaper 驱动 |

### 5.2 两段式部署（重要）

API 函数需要 worker 的触发器地址（`WORKER_URL`），而该地址要等 worker 创建后才有，且 `fcapp.run` 子域含随机串、无法由 region + 函数名推导。因此：

```bash
# ---- 第 0 步：把账号相关取值全部导出（缺一个都会让 s deploy 报错）----
export REDIS_URL=redis://:password@r-xxxx.redis.rds.aliyuncs.com:6379/0
export CLOUD_ASR_API_KEY=sk-xxxx
export DEEPSEEK_API_KEY=sk-xxxx
export WORKER_TOKEN=$(openssl rand -hex 24)
export CORS_ORIGINS=https://your-frontend.vercel.app
# VPC 三个 ID（§4.2）；这三个没有默认值，缺了 s deploy 会直接失败
export FC_VPC_ID=vpc-bp1xxxxxxxxxxxx
export FC_VSWITCH_ID=vsw-bp1xxxxxxxxxxxx
export FC_SECURITY_GROUP_ID=sg-bp1xxxxxxxxxxxx
# 可选：自定义函数角色 ARN，留空则用 FC 服务关联角色（§4.3）
# export FC_ROLE_ARN=acs:ram::<账号ID>:role/<角色名>

# ---- 第一段：先建出两个函数（WORKER_URL 留空不阻断启动，只是分发会失败）----
# --skip-push：镜像已在 ACR 里（§3.1 构建），禁止 s deploy 在本地
#   docker login / docker push。本地 Docker 登不上 ACR 时这个开关是必需的。
s deploy -t s.yaml --skip-push

# ---- 取 worker 的 HTTP 触发器地址 ----
s info -t s.yaml
# 输出里 worker 函数下的 URL 形如：
#   https://video-to-text-worker.abc123def.cn-hangzhou.fcapp.run

# ---- 第二段：把该地址写进部署机环境变量后重新部署 ----
export WORKER_URL=https://video-to-text-worker.abc123def.cn-hangzhou.fcapp.run
s deploy -t s.yaml --skip-push

# ---- 确认 VPC / 角色真的落到了函数上（别只看部署成功的绿字）----
s info -t s.yaml
# 期望：两个函数都能看到 vpcConfig（vpcId / vSwitchIds / securityGroupId）
#       与 role（空 = 服务关联角色）
```

> `WORKER_URL` 漏配时应用**仍能启动**（不阻断引导），但每次 `POST /api/transcribe` 都会明确失败：任务被标 `failed`（`TRANSCRIBE_FAILED`「任务分发失败，请重试」）+ 返回 500。这是刻意设计——宁可明确报错，也不静默丢任务。
>
> `FC_VPC_ID` / `FC_VSWITCH_ID` / `FC_SECURITY_GROUP_ID` 则**故意不给默认值**：留空会导致「部署成功、运行期连不上 Redis」，比部署时直接报 `FC_VPC_ID is not defined` 难查得多。

### 5.4 关键字段说明

- **`vpcConfig`（S2-Deploy）**：两个函数**必须配置成完全相同的一组** `vpcId` / `vSwitchIds` / `securityGroupId`，否则任务状态写不到同一个 Redis（api 能写、worker 读不到）。三个字段在 fc3 schema 里都是必填，缺一个直接部署失败。`vSwitchIds` 是**列表**（schema 允许 `array` 或字符串 `"auto"`，但不接受单个裸字符串 ID）。交换机必须落在 FC 支持的可用区，否则报 `InvalidArgument: vSwitch is in unsupported zone`；不在列表里就同 VPC 内新建一个交换机，别重建 Redis。详见 §4.2。
- **`internetAccess: true` 与 `vpcConfig` 并存**：FC 默认「可访问公网、不可访问 VPC」，配了 VPC 后仍需显式开 `internetAccess` 才能同时走公网。关掉它会让 B站下载、云端 ASR、DeepSeek 全部失败，而 Redis 反而正常——排查方向会被带偏。
- **`role`（S2-Deploy）**：函数**执行角色**，留空即用 FC 服务关联角色 `AliyunServiceRoleForFC`（已覆盖 SLS 日志、VPC ENI、同账号 ACR 拉取）。它**不是** ACR 拉取或 VPC 通路的开关，只有「函数代码里访问其他云服务」才需要填。详见 §4.3。
- **`port` 必须等于 uvicorn 实际监听端口**（模板统一用 9000）。端口不一致的典型表现是函数部署成功但请求 502/超时、健康检查一直失败。
- **`entrypoint` / `command` 语义与 Docker 一致**：`entrypoint` 是可执行文件（`uvicorn`），`command` 是参数。镜像本身 `CMD` 监听 8000，模板通过这两个字段覆盖为 9000。
- **`instanceConcurrency: 1`（worker）**：一实例一作业，内存模型最简单。100MB 音频全程在内存 + ffmpeg 转码，并发跑多作业容易 OOM。
- **`concurrencyConfig.reservedConcurrency: 5`（api，P2 防刷爆额度）**：FC3 **没有函数级 `maxInstance` 字段**（`ALIYUN::FC3::Function` 属性全集里没有它，写了会被 schema 拒或被静默忽略）。按量实例上限走独立的弹性配置 API（`PutScalingConfig`），`s.yaml` 侧对应 `concurrencyConfig`。效果：最多 5 个实例 × `instanceConcurrency: 10` = 50 并发封顶，防住「被人刷接口把 ASR/LLM 额度烧穿」。调大前先确认账号的弹性实例配额。
- **`timeout: 1800`（worker）**：长视频 ASR 需要。请对照当前地域配额确认上限，超限需在配额中心申请。
- **`TASK_INIT_MODE`**：api = `recover`，worker = **`none`**。Worker 每次冷启动都跑初始化，`recover` 会把其他实例正在跑的任务误判为 `interrupted`（代码里也显式传 `recover_interrupted=False`，双保险）。
- **`asyncInvokeConfig`**：`maxAsyncRetryAttempts: 3`（FC 默认）、`maxAsyncEventAgeInSeconds: 604800`（与 `TASK_TTL_SECONDS` 7 天对齐）。
- **`reapTimer`（定时触发器，S2-P4）**：每 5 分钟触发一次僵尸任务清理。**关键约束：定时触发器是「事件调用」，不能指定 HTTP 路径**——FC 固定把 event `POST` 到容器的 `/invoke`（详见[自定义运行时 Web 函数](https://help.aliyun.com/zh/functioncompute/web-functions)）。因此 reaper 不能只挂在 `/internal/worker/reap` 上（那个端点 Timer 打不到，只留给本地脚本与人工 curl），`worker_app` 的 `/invoke` 会按 `triggerTime` + `triggerName` 特征分流到 reaper。cron 为 **6 段**（秒 分 时 日 月 周），最小粒度 1 分钟。
- **定时触发器的凭据放在 payload 里（P0-2）**：FC 定时触发器**无法自定义请求头**，所以它带不了 `X-Worker-Token`。而 `/invoke` 是 `authType: anonymous` 的公网入口，绝不能「看到 `triggerTime`/`triggerName` 就跳过鉴权」——任何人都能伪造这两个字段触发 reaper。因此 `s.yaml` 的 `reapTimer.payload` 写成 `{"action":"reap","token":"${env('WORKER_TOKEN', '')}"}`，`worker_app._timer_payload_token` 从中取出 token，走与请求头**完全相同**的校验。改 `WORKER_TOKEN` 后必须重新 `s deploy`（payload 在部署时渲染）。
- **`authType: anonymous` + `WORKER_TOKEN`**：官方文档明确「无需认证时可在业务中校验请求头 `Authorization` 是否合法以自行鉴权」。worker 的触发器只开 `POST`，鉴权由应用层 `WORKER_TOKEN` 兜底。若需更严可改 `authType: function`（签名认证），但会重新引入签名 SDK——这正是 P3 刻意规避的。
- **内置域名仅用于测试**：官方说明 `*.fcapp.run` 只适合联调，对外服务应绑已备案的自定义域名。上线前请替换。
- **`code: ./backend`**：自定义容器（`runtime: custom-container`）**只使用镜像**，fc3 在部署前会主动 `unset` 掉 `code`（见 `resources/fc/impl/replace-function-config.ts`），代码目录不会被打进镜像。保留该字段只是为了将来若切回非容器运行时可直接复用，删掉也不影响部署。

### 5.5 环境变量怎么注入

`s.yaml` 里所有密钥都写成 `${env('NAME')}`，由部署机环境变量提供，仓库不含明文：

```bash
export REDIS_URL=redis://:password@r-xxxx.redis.rds.aliyuncs.com:6379/0
export CLOUD_ASR_API_KEY=sk-xxxx
export DEEPSEEK_API_KEY=sk-xxxx
export WORKER_TOKEN=$(openssl rand -hex 24)
export CORS_ORIGINS=https://your-frontend.vercel.app
export WORKER_URL=https://video-to-text-worker.abc123def.cn-hangzhou.fcapp.run
# 网络（§4.2）与可选角色（§4.3）
export FC_VPC_ID=vpc-bp1xxxxxxxxxxxx
export FC_VSWITCH_ID=vsw-bp1xxxxxxxxxxxx
export FC_SECURITY_GROUP_ID=sg-bp1xxxxxxxxxxxx
# export FC_ROLE_ARN=acs:ram::<账号ID>:role/<角色名>
s deploy -t s.yaml
```

> **语法必须带引号**：Serverless Devs 的魔法变量是 `${env('NAME')}` / `${env('NAME', '默认值')}`（内部按 JS 表达式求值）。写成 `${env(NAME)}` 会抛 `NAME is not defined`，`s deploy` 直接失败。模板里 `WORKER_URL` / `WORKER_TOKEN` / `CORS_ORIGINS` / `FC_ROLE_ARN` 都带了默认值，缺省不会报错；`FC_VPC_ID` / `FC_VSWITCH_ID` / `FC_SECURITY_GROUP_ID` **故意没有默认值**，缺了会在部署时就失败（比运行期连不上 Redis 好查）。

> **别把这三个 ID 写进仓库**：它们是账号内资源标识。想少敲几次可以放在一个不被提交的 `deploy.env` 里，`set -a; . ./deploy.env; set +a` 后再 `s deploy`。

控制台等价路径（不想用 `s deploy` 时）：函数计算 3.0 → 创建函数 → **自定义容器** → 选择 ACR 镜像 → 监听端口填 9000 → **高级配置 → 网络：允许访问 VPC 选「自定义配置」并填 VPC/交换机/安全组，允许函数访问公网保持开启** → 配置环境变量 / 实例规格 → 创建 HTTP 触发器（api 开 GET+POST，worker 只开 POST + 一个定时触发器）。控制台路径容易漏掉「网络」那一步，漏了就是本文件反复强调的静默降级现象。

---

## 6. 环境变量清单

先分清两类变量，别混：

| 类别 | 注入位置 | 本节的变量 |
| --- | --- | --- |
| **部署机变量** | 由 `s deploy` 读取（`${env('NAME')}`），**不会进入容器** | `FC_VPC_ID`、`FC_VSWITCH_ID`、`FC_SECURITY_GROUP_ID`、`FC_ROLE_ARN` |
| **函数运行时变量** | 写进 `environmentVariables`，容器内 `os.getenv` 可读 | 下面 §6.1 / §6.2 的全部 |

`FC_VPC_ID` / `FC_VSWITCH_ID` / `FC_SECURITY_GROUP_ID` 是账号内的资源 ID，只在部署时用来生成 `vpcConfig`，**故意没有默认值**——缺了 `s deploy` 会直接报 `xxx is not defined`，比「部署成功但运行期连不上 Redis」好查得多（§4.2）。

### 6.1 必填（4 项 + `ENQUEUE_MODE=fc` 时多 1 项）

| 变量 | 示例 | 说明 |
| --- | --- | --- |
| `TASK_STORE` | `redis` | 必须。`memory` 会写实例内 SQLite（`/app/data`），实例回收即丢，且多实例互不可见 |
| `REDIS_URL` | `redis://:password@r-xxxx.redis.rds.aliyuncs.com:6379/0` | 必须。任务状态 + 限流共享计数。未配置会**静默降级** memory 并打警告 |
| `CLOUD_ASR_API_KEY` | `sk-xxxx` | 必须。未配置时提交云端转写直接报 `CLOUD_NOT_CONFIGURED` |
| `DEEPSEEK_API_KEY` | `sk-xxxx` | 必须（总结）。未配置时 `/api/summarize` 报 `SUMMARIZE_NOT_CONFIGURED` |
| `WORKER_URL` | worker 的 `*.fcapp.run` 地址 | **仅 API 函数、仅 `ENQUEUE_MODE=fc` 时必填**（见 §6.2） |

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
| `REAPER_QUEUED_STALE_SECONDS` | `1800` | **排队中**任务（`started_at` 为空，没有心跳）的判定阈值，`s.yaml` 里 API 与 Worker 两个函数都显式声明。实际生效取 `max(TASK_STALE_SECONDS, 本项)`——否则队列一堵十几分钟就把还没轮到跑的任务判成超时。Worker 的 `instanceConcurrency` 是 `1`，一次只跑一个任务，**派发积压时请按「最长排队时长」调大本项**（例如 10 个长视频依次 8 分钟 ≈ 72 分钟，就要设到 3600 以上） |
| `TASK_HEARTBEAT_SECONDS` | `30` | 长阻塞步骤（下载 / FFmpeg / ASR）靠心跳保活；0 = 关闭 |
| `ENQUEUE_MODE` | `fc`（API）/ `inline`（Worker） | 任务分发模式。`inline` = 同进程 BackgroundTasks；`fc` = 投递到 worker 触发器（异步）；`mns`/`redis` 为后续实现位，当前显式报错 |
| `WORKER_URL` | worker 的 `*.fcapp.run` 地址 | **`ENQUEUE_MODE=fc` 时必填**。`s info -t s.yaml` 里取；无法由 region + 函数名推导 |
| `WORKER_PATH` | `/internal/worker/transcribe` | worker 上的作业路由，须与 `WORKER_PATH` 一致 |
| `ASYNC_TIMEOUT_SECONDS` | `5` | 异步投递超时：只等 FC 回 202，不等作业跑完 |
| `TASK_LOCK_SECONDS` | `3600` | Worker 执行锁 TTL（`SET NX EX`），执行期间按心跳续期；崩溃后到期自动释放 |
| `REAPER_LOCK_SECONDS` | `120` | Reaper 互斥锁 TTL（S2-P4）：只需覆盖单次扫描耗时，与执行锁无关 |
| `WORKER_TOKEN` | 空 | Worker 端点鉴权，**必填**（P0 fail-closed）：为空时 Worker 所有入口返回 503。非空时要求 `X-Worker-Token`、`Authorization: Bearer <token>` 或定时触发器 payload 里的 `token` 三者之一匹配 |
| `BILI_USER_AGENT` | 空 / 真实浏览器 UA | B站风控覆盖项。留空沿用 yt-dlp 自带 UA；机房 IP 仍 412 时填一个真实浏览器 UA |
| `BILI_COOKIE` | 空 / 浏览器整条 Cookie | B站风控兜底。含账号凭证，只经部署机环境变量注入，绝不提交仓库 |
| `CLOUD_ASR_BASE_URL` / `CLOUD_ASR_MODEL` | 见 `.env.example` | 换服务商/模型时覆盖 |

> **⚠️ 环境变量键名不能以 `FC_` 开头。** 阿里云把 `FC_*` 保留给系统环境变量（`FC_FUNC_CODE_PATH`、`FC_HANDLER`、`FC_RUNTIME_API` 等），在函数上设置该前缀的自定义变量会被平台直接拒绝，表现为 **`s deploy` 阶段失败**（不是运行期报错，因此很难和业务代码联系起来）。所以 worker 地址/路径/超时用的是 `WORKER_URL` / `WORKER_PATH` / `ASYNC_TIMEOUT_SECONDS`。
>
> 注意区分两类变量：`FC_VPC_ID` / `FC_VSWITCH_ID` / `FC_SECURITY_GROUP_ID` / `FC_ROLE_ARN` 是**部署机**变量，只在 `${env('...')}` 里被求值、不会写进函数环境变量，因此不受保留字规则约束。

`TASK_DB_PATH`、`WHISPER_*`、`ENABLE_VOCAL_SEPARATION` 在 FC 上都不需要。

> **不需要** `FC_ACCOUNT_ID` / `FC_REGION` / `FC_WORKER_FUNC_NAME` / `ALIBABA_CLOUD_ACCESS_KEY_*`：这些只在走 OpenAPI `InvokeFunction`（需 ACS3 签名）时才需要。P3 走 HTTP 触发器 + `X-Fc-Invocation-Type: Async`，不签名、不常驻 AK/SK。

### 6.3 密钥管理：建议走 KMS

**不要把 Key 写进 s.yaml 提交到 git。** 分层建议：

1. **部署机侧（当前即可用）**：s.yaml 用 `${env('REDIS_URL')}` / `${env('CLOUD_ASR_API_KEY')}` 引用部署机环境变量，由 CI 或本地 shell 注入；仓库里只保留占位符。这是本文示例采用的方式。
2. **KMS 凭据管家（生产推荐）**：在 KMS / 凭据管家创建凭据，函数执行角色只授予读取该凭据的权限（最小权限），部署流水线从 KMS 取值后注入 FC 环境变量。这样密钥不落在仓库、不落在开发者机器上。
3. **运行时拉取**：函数启动时用阿里云 SDK 调 `GetSecretValue` 再写入 `os.environ`。**本仓库当前没有内置 KMS 客户端**，这属于 S2 的应用层改造；在改造前，请用方案 1/2，并接受「FC 控制台里能看到明文环境变量」这一事实——用 RAM 权限和操作审计兜底。

补充：环境变量在 FC 控制台/API 中可被有权限的 RAM 用户读取，务必限制 `fc:UpdateFunction` / `fc:GetFunction` 的授权范围，并开启操作审计。KMS 的具体字段与授权策略以当前 FC / KMS 官方文档为准（本仓库未做端到端验证）。

---

## 7. 部署后验收

### 7.1 控制台验证全链路（API → Worker → Redis）

目标：**不依赖任何本地脚本**，只在阿里云控制台里跑一次真实视频转写，并确认任务状态确实落在 Redis（而不是某个实例的内存里）。

> 为什么必须查 Redis：`REDIS_URL` 连不上时应用会**静默降级到 memory**（只打一条警告）。此时 API 和 Worker 各自持有一份任务，表现是「任务永远停在 `processing`」，而 HTTP 接口看起来一切正常。只看接口返回会误判成「链路通了」。

#### 步骤 0：确认 VPC 真的落到了函数上

```bash
s info -t s.yaml
```

两个函数（`video-to-text-api` / `video-to-text-worker`）都应能看到 `vpcConfig` 的 `vpcId` / `vSwitchIds` / `securityGroupId`，且**三者完全相同**。看不到就说明 §5.2 的 `s deploy` 没带环境变量，先补齐再往下走。

#### 步骤 1：在控制台取 API 函数的公网地址

函数计算控制台 → **函数管理 → 函数列表** → 地域选 `cn-hangzhou` → 点 `video-to-text-api` → **触发器**页签 → `httpTrigger` 那一行的「公网访问地址」。

形如 `https://video-to-text-api.<随机串>.cn-hangzhou.fcapp.run`。把它记下来，后面几步要用：

```bash
API_URL=https://video-to-text-api.<随机串>.cn-hangzhou.fcapp.run
```

#### 步骤 2：在控制台「测试函数」里发一次真实转写请求

同一个函数详情页 → **测试函数**页签（Web 函数以 HTTP 请求方式测试）：

| 项 | 值 |
| --- | --- |
| 请求方法 | `POST` |
| 路径 | `/api/transcribe` |
| 请求头 | `Content-Type: application/json` |
| 请求体 | `{"videoId":"BV1xxxxxxxxx","engine":"cloud"}` |

期望响应 `200`：

```json
{"taskId":"<32 位十六进制>","status":"processing","stage":"parse_link","progress":0}
```

- `videoId` 换成任意**真实存在**的 B站视频；`engine` 必须是 `cloud`（镜像不含本地 ASR）。
- 这一步会真实调用云端 ASR，**产生真实费用**，别拿长视频反复压测。
- 若当前控制台版本的测试面板不支持自定义请求方法与请求体（各版本文案不同：`测试函数` / `在线测试`），直接用 §7.3 的 `curl`——两者等价，验证目标一致。
- 返回 `404`：路径写错（注意不要带 `/backend-api` 前缀，那是 Vercel 侧的代理前缀）。
- 返回 `422`：请求体字段不对（`videoId` 是必填）。
- 返回 `500` + `{"error":{"code":"TRANSCRIBE_FAILED","message":"任务分发失败，请重试"}}`：API 侧投递失败，查 §5.2 的 `WORKER_URL`。

#### 步骤 3：在 Worker 函数日志里确认作业被异步执行

控制台 → `video-to-text-worker` → **日志**页签（或 `s logs -t s.yaml --function-name video-to-text-worker --tail`）。

期望依次看到：

```
Worker 领取任务（task_id=...）        ← 抢锁成功
stage 推进：parse_link → extract_audio → asr → completed
```

- 完全没有日志：API 的异步投递没成功（查 api 日志的 `FC 投递被拒` / `FC 投递失败`）。
- 日志里是 `skipped`（`reason=locked` 或 `already_*`）：任务被重复投递，属正常幂等；看有没有另一次执行把它跑完。

#### 步骤 4：在 Redis 里确认状态流转（这一步才是「全链路」的证据）

阿里云 Redis 控制台 → 目标实例 → **登录**（或 DMS for Redis）→ 执行：

```
GET task:<taskId>
```

期望看到任务 JSON，并且：

| 检查点 | 期望 | 说明 |
| --- | --- | --- |
| `status` | `processing` → `completed` | 轮询几次能看到变化 |
| `transcript` | 非空数组 | 真实转写结果 |
| `updated_at` | 递增 | 心跳写入生效 |
| `TTL task:<taskId>` | 约 `604800` | 滑动续期生效（7 天） |
| `GET task:lock:<taskId>` | 执行期间存在、完成后消失 | `release` 带 token 正常释放 |

**若 `GET task:<taskId>` 返回 `(nil)`，但 `GET /api/transcribe/<taskId>` 能查到任务**：说明函数降级到了 memory 存储，Redis 根本没连上。按顺序查：

1. 函数上 `vpcConfig` 是否真的存在（步骤 0）；
2. Redis 白名单是否放行了**函数所在交换机**的网段；
3. 安全组出方向是否放行 `6379` 与 `ICMP`；
4. `REDIS_URL` 是否用了实例的**内网地址**（不是公网地址）、端口 `6379`、密码做了 URL 编码；
5. worker 日志里的 `redis 降级` 警告。

#### 步骤 5：接口侧确认

```bash
TASK=<步骤 2 拿到的 taskId>
curl -s "$API_URL/api/transcribe/$TASK" | jq '{status,stage,progress,transcriptSource}'
```

期望 `status=completed`，`transcriptSource` 为 `subtitle_cc` / `subtitle_ai` / `asr` 之一（取决于该视频是否有字幕）。

> 顺带验一次 reaper：控制台 → `video-to-text-worker` → 测试函数里对 `/internal/worker/reap` 发 POST（带 `X-Worker-Token`），期望 `{"status":"ok","scanned":N,"reaped":0}`。详见 §7.3.1。

### 7.2 基础连通

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

### 7.3 异步链路验收（S2-P3 核心）

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
| 第 1 步返回 `404` | 异步调用**没有透传 path**（见 §8.4 R1），需把 `WORKER_PATH` 改成实际收到的路径 |
| 第 1 步返回 `401` | api/worker 两侧 `WORKER_TOKEN` 不一致 |
| 第 1 步返回 `403` / `access denied` | 触发器认证方式不是 `anonymous` |
| 第 2 步一直 `processing` 且 `attempts` 不增 | api 侧投递失败，查 api 日志的 `FC 投递被拒` / `FC 投递失败` |
| 第 2 步 `attempts` 增长但 `stage` 不动 | worker 侧问题，查 worker 日志 |
| 第 2 步变 `completed` 且 `transcriptSource` 为 `subtitle_cc` / `subtitle_ai` / `asr` | 全链路打通 |
| 任务 `failed`（「任务分发失败，请重试」） | `WORKER_URL` 漏配或写错，见 §5.2 |

> `attempts` 字段在 `GET /api/transcribe/{taskId}` 的响应里不返回（前端契约未变），需查 Redis `task:{taskId}` 的 JSON。

### 7.3.1 Reaper 验收（S2-P4）

三种验法，从便宜到真实：

```bash
# A) 人工触发端点（最直接）：Worker 有 HTTP 触发器，可以直接 curl
curl -s -X POST "$WORKER_URL/internal/worker/reap" -H "X-Worker-Token: $WORKER_TOKEN" | jq
#    期望 {"status":"ok","scanned":N,"reaped":M}；没有僵尸任务时 reaped=0

# B) 本地脚本（不碰云上）：在 backend/ 目录，连的是 .env.local 里的 Redis
cd backend && python -m scripts.reap --dry-run   # 只列出会被判死的任务
cd backend && python -m scripts.reap             # 真收割

# C) 验定时触发器是否真的通（这是唯一能证明 /invoke 分流有效的办法）
#    手动把一条任务改成陈旧，然后等 5 分钟，看 worker 日志
#    期望日志：收到定时触发器事件，执行僵尸任务清理
s logs -t s.yaml --function-name video-to-text-worker --tail
```

造一条僵尸任务（把 `updated_at` 回拨 1 小时，并置上 `started_at` 表示「Worker 已在跑」）：

```bash
redis-cli --eval /dev/stdin task:$TASK <<'LUA'
local raw = redis.call('GET', KEYS[1])
local t = cjson.decode(raw)
t.updated_at = t.updated_at - 3600
t.started_at = t.updated_at   -- 关键：为空表示「还在排队」，会走 1800s 的宽阈值
redis.call('SET', KEYS[1], cjson.encode(t), 'EX', 604800)
LUA
```

> `started_at` 为空（还在排队）时 reaper 用的是 `max(TASK_STALE_SECONDS, REAPER_QUEUED_STALE_SECONDS)`（默认 1800s）——
> 回拨 3600s 仍然会被收割，但若只回拨 10 分钟就**不会**，那是预期行为（见 §6 环境变量表）。

**判读表：**

| 现象 | 定位 |
| --- | --- |
| A 返回 `{"status":"ok",...,"reaped":1}`，任务变 `failed`（`TASK_TIMEOUT`） | reaper 逻辑正确 |
| A 返回 `{"status":"skipped","reason":"locked"}` | 另一个 reaper 正在跑（120s 内重试即可），不是故障 |
| A 返回 `401` | `WORKER_TOKEN` 不匹配 |
| C 等 5 分钟仍无日志 | 定时触发器没建成功（`s info` 查 `reapTimer`），或 `enable: false` |
| C 有日志但报 `422 事件体无法解析` | `/invoke` 分流没生效——检查 `_looks_like_timer_event` 与 event 字段名 |
| C 日志里是 `POST /invoke 401 Unauthorized`（每 5 分钟一条） | 定时事件被拒。**先查 `reapTimer.payload` 里有没有 token**（`s info` 看触发器配置）——它必须是 `{"action":"reap","token":"<WORKER_TOKEN>"}`。注意 payload 在**部署时**渲染，改过 `WORKER_TOKEN` 却没重新 `s deploy` 就会对不上 |
| 任务变 `failed` 但前端只显示「出了点问题」 | `error-alert.tsx` 的 `ERROR_TITLES` 缺该错误码。P2 已改成 `Record<ClientErrorCode, string>` 强制穷举，漏一个就编译不过；若仍出现，检查后端是否返回了契约外的码 |

> **不要**把 `WORKER_PATH` 配成 `/internal/worker/reap`——那是人工入口，定时触发器永远打不到自定义路径。

### 7.3.2 B站 412 诊断（设备指纹 + playurl API）

机房出口 IP 访问 B站会被风控，典型表现是任务停在 `extract_audio`、`error.message` 含
`HTTP Error 412: Precondition Failed`。**注意这与代码和视频都无关**：同一份镜像在本地
家庭宽带下能正常解析。

#### 先确认是哪一类 412

worker 上有诊断端点（需 `WORKER_TOKEN`，可用 `?bvid=` 指定视频）：

```bash
curl -X POST "https://<worker-域名>/internal/worker/diagnose?bvid=BV1uv411q7Mv" \
  -H "X-Worker-Token: <WORKER_TOKEN>"
```

返回是**对照组**：同一出口 IP、同一批 URL，只有请求头不同。

```json
{
  "bvid": "BV1uv411q7Mv",
  "egress": {"bili_cookie_configured": false, "bili_user_agent_configured": false,
             "device_fingerprint_present": true},
  "baseline": {"view_api": {"status": 412}, "video_page": {"status": 412},
               "finger_spi": {"status": 200}},
  "hardened": {"view_api": {"status": 200, "code": 0}, "video_page": {"status": 412},
               "finger_spi": {"status": 200}},
  "video_page_variants": {"cookie_only": {"status": 412, "bytes": 3400},
                          "warm_session": {"status": 412, "bytes": 3400}},
  "audio_path": {"ok": true, "bandwidth": 315098, "mime_type": "audio/mp4",
                 "backup_urls": 2, "cdn_status": 200},
  "subtitle_tracks": 12
}
```

这个输出是**实测结果**，不是示意。关键结论：

- `hardened.view_api` 200 → **JSON API 带指纹能通**
- `hardened.video_page` 412 → **网页即使带指纹 + 首页会话 Cookie（`b_nut`、
  `X-BILI-SEC-TOKEN`）依然 412**，返回 3400 字节风控页
- `audio_path.ok` true → **playurl API 与音频 CDN 都能用**

判读方式：

| 现象 | 结论 | 处理 |
| --- | --- | --- |
| `view_api` 412，`finger_spi` 200 | 没带 buvid，B站按爬虫处理 | 默认配置即可，代码已自动取指纹 |
| `view_api` 200 但 `video_page` 412 | **网页路径被封，这是常态** | 不用管：生产下载走 playurl API，不碰网页（见下） |
| `audio_path.ok` false | playurl 也失败了 | 看 `error` 原文；配 `BILI_USER_AGENT` → `BILI_COOKIE` |
| `finger_spi` 也 412/超时 | 出口 IP 被整体拉黑 | 只能换出口（NAT 网关 / 换地域），或配 `BILI_COOKIE` |
| 以上都正常但任务仍失败 | 不是 412 问题 | 看 §9 排错表其他条目 |

#### 两条路径的分工

**字幕快路径**（有 CC 字幕的视频）：走 yt-dlp CC 或 dm/view API，命中即秒级返回。

**音频下载**（无 CC 字幕的视频）：`download_audio` 三级降级，**第一级就是 playurl API**：

| 级别 | 手段 | 是否碰网页 |
| --- | --- | --- |
| 1 | `playurl` API（`/x/player/playurl?fnval=16`）拿 DASH 音频直链 | 否 |
| 2 | yt-dlp 解析直链 | 是（机房 IP 必 412） |
| 3 | yt-dlp 下载到临时目录 | 是（同上） |

`backend/app/services/bili_playurl.py` 的实现要点：

- **免 wbi 签名**：`/x/player/playurl` 未登录就返回完整 DASH，不必引入签名依赖
- `fnval=16` 才返回 `dash.audio[]`；按 `bandwidth` 挑最高档（通常 30280 = 192K）
- **CDN 必须带 UA + Referer + Cookie**：实测缺任一个都是 403 + text/html
- 下载侧只拒绝明确的文本类型（`text/*`、`application/json`、`application/xml`），
  **`application/octet-stream` 必须放行**——云端 CDN 把 mp4 报成 octet-stream，
  本地报 `video/mp4`。这个差异曾导致云端把合法音频误判成错误页、静默回退到
  必然 412 的 yt-dlp，表现为「明明 playurl 是通的，任务却报 412」

#### 设备指纹的作用

浏览器首次访问 B站时会被下发 `buvid3` / `buvid4`（存在 Cookie 里，有效期很长），
之后所有请求都带着它。未登录也能拿到：`GET /x/frontend/finger/spi` 返回 `b_3` / `b_4`。

代码里的实现（`backend/app/services/bili_fingerprint.py`）：

- 进程内缓存 12 小时，避免频繁请求（频繁取指纹本身就是可疑行为）；
- 取失败**静默降级为空**并冷却 5 分钟——指纹是缓解手段，绝不能反过来把转写打挂；
- 配了 `BILI_COOKIE` 就不再取指纹（登录 Cookie 自带 buvid3，混入另一设备指纹反而可疑）。

> 指纹值只在内存里，不写日志、不出现在诊断响应中（只回 `device_fingerprint_present: true/false`）。

### 7.4 日志

```bash
s logs -t s.yaml --function-name video-to-text-api --tail
s logs -t s.yaml --function-name video-to-text-worker --tail
```

优先看：容器启动日志（uvicorn 是否 listen 成功）、`redis` 降级警告、`已投递到 FC（task_id=..., request_id=...）`。

### 7.5 前端切换（Vercel）

```bash
# Vercel 环境变量（Production + Preview 都要配）
BACKEND_ORIGIN=https://video-ttext-api-thuxmkmghu.cn-hangzhou.fcapp.run
NEXT_PUBLIC_USE_MOCK=false        # 构建期常量，改完必须重新构建
```

**变量必须在部署前配好**：`NEXT_PUBLIC_USE_MOCK` 在 `next build` 时被内联进客户端包，
`BACKEND_ORIGIN` 在构建时固化成 `rewrites` 的 target（写进 `.next/routes-manifest.json`）。
事后改环境变量必须重新部署才生效。

CLI 参考：

```bash
vercel link --yes
vercel env add NEXT_PUBLIC_USE_MOCK production   # 输入 false
vercel env add BACKEND_ORIGIN production         # 输入后端地址
vercel --prod --yes
```

#### 验证构建产物真的带上了变量

环境变量在 Vercel 上是加密存储的，`vercel env ls` 只显示 `Hidden`。想确认值被正确带进构建，
用 `vercel pull` + 本地构建，然后看固化的 rewrite 目标：

```powershell
vercel pull --yes --environment production
vercel build --yes --environment production
(Get-Content .next\routes-manifest.json -Raw | ConvertFrom-Json).rewrites[0].destination
# 期望：https://<api-域名>/api/:path*
```

> 在 Windows 上 `vercel build` 末尾可能因 symlink 报 `EPERM`（生成 `.vercel/output` 失败），
> 但 `.next/` 已经编译完成，上面的检查依然有效。查完记得 `Remove-Item .vercel\output`，
> 并重新 `pnpm build`（`vercel build` 会用生产变量覆盖 `.next`）。

#### 两个默认开启的坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 打开域名要求登录 Vercel | 项目默认开了 **Deployment Protection**（`ssoProtection.deploymentType = all_except_custom_domains`），所有 `*.vercel.app` 都要登录 | 关掉：项目 Settings → Deployment Protection → Vercel Authentication → Disable；或 `PATCH /v9/projects/<name> {"ssoProtection":null}` |
| 国内本机打不开 `*.vercel.app` | `*.vercel.app` 在国内被 **DNS 污染 + TLS SNI 阻断**（解析到 `173.236.212.42` 等黑洞 IP，直连 `76.76.21.21` 也被 reset） | 不是部署问题。换网络/加代理验证；`vercel.com`、`api.vercel.com` 是通的，CLI 可以正常用 |

#### CORS 不需要额外配置

前端所有请求都是相对路径 `/backend-api/*`（见 `src/lib/real-api.ts`），由 Next 的
`rewrites` 在**服务端**转发到 `BACKEND_ORIGIN`。浏览器只与 Vercel 同源通信，
**不产生跨域请求**，因此 FC 的 `CORS_ORIGINS` 填不填都不影响前端。

只有在「浏览器直连 FC 地址」时（比如直接用 `curl` 或另一个域名的手写 fetch）才需要它。
当前函数已放行 `*`（`deploy.ps1` 里 `CORS_ORIGINS='*'`），预检实测：

```bash
curl -X OPTIONS https://<api-域名>/api/transcribe \
  -H "Origin: https://xxx.vercel.app" \
  -H "Access-Control-Request-Method: POST"
# access-control-allow-origin: *
```

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
| 函数超时 | 同步总结 30-70 秒，长视频可到数分钟；worker 长转写 | api `timeout` ≥ 300 秒；worker `timeout` 1800 秒（超限需申请配额） |
| 冷启动 + 406MB 镜像 | 首个请求慢 | ACR 同地域 / 镜像加速；必要时预留实例 |
| 静态 FFmpeg 为 GPLv3 | 对外分发镜像需遵守 GPL | 保留许可声明；如不可接受，改用 apt 版 FFmpeg（镜像约 +400MB） |
| 无本地 ASR | `ASR_ENGINE=local` 不可用 | 固定 `cloud`，`ASR_ENGINE_FALLBACK` 留空 |
| B站风控 / yt-dlp 失效 | 解析或字幕失败 | 定期重建镜像升级 `yt-dlp`；关注 `EXTRACT_TIMEOUT_SECONDS` |
| 任务在 `extract_audio` 阶段失败，`error.message` 含 `HTTP Error 412: Precondition Failed` | **网页路径被封**：`www.bilibili.com/video/*` 在机房 IP 上即使带指纹 + 会话 Cookie 也是 412 | **已修**：`download_audio` 第一级走 `playurl` API 取 DASH 音频直链，完全不碰网页（见 §7.3.2）。仍失败时按顺序加：`BILI_USER_AGENT` → `BILI_COOKIE` |
| 有 CC 字幕的视频正常，无字幕的必失败 | 同上：字幕路径走 API，音频路径原本走网页 | 已修，同上一行 |
| 诊断显示 `audio_path.ok: true` 但任务仍报 412 | playurl 通了却静默回退 yt-dlp（通常是下载侧把合法音频误判成错误页） | 已修：只拒绝 `text/*` 等明确文本类型，`application/octet-stream` 放行（云端 CDN 用这个类型） |
| `error.message` 里出现 `\u001b[0;31m` 之类乱码 | yt-dlp / ffmpeg 的终端颜色码被拼进消息 | 已修：`ytdlp_options.base_options()` 统一 `no_color=True`，`AppException` 出口再 `strip_ansi` 兜底。若再出现，检查是否有新的 `yt_dlp.YoutubeDL` 调用点没走公共选项 |
| Redis 任务无 TTL | 任务记录无限增长 | **S2-P0 已修**：`TASK_TTL_SECONDS`（默认 7 天，滑动续期）；0 = 永久保留 |
| 冷启动即改写任务状态 | 多实例下误杀在途任务 | **S2-P0 已修**：只把「陈旧」（`TASK_STALE_SECONDS` 内无心跳）的 `processing` 判为 `interrupted`；Worker 侧设 `TASK_INIT_MODE=none`。**P1 补强**：排队中的任务（`started_at` 为空）用 `REAPER_QUEUED_STALE_SECONDS`（1800s）判定 |
| 异步调用 Body ≤ 128 KB | 事件体过大会被 FC 拒收 | `TranscribeJob.to_dict()` 仅几百字节；**不要往事件体里塞文字稿**（P1 只传入参的设计已保证） |
| Worker 崩溃后重试被锁挡住 | 任务停在 `processing` | **P4 已修**：worker 函数上的定时触发器（`reapTimer`，每 5 分钟）扫描心跳超时（`TASK_STALE_SECONDS=600`）的 `processing` 任务，标 `failed` + `TASK_TIMEOUT`。见 §7.3.1 |
| 未配 `vpcConfig` | 连不上 VPC 内网 Redis，**静默降级 memory** | 多实例各持一份任务，表现为「任务永远 `processing`」；接口看起来却正常。必须给两个函数配同一套 `vpcConfig`（§4.2） |
| 配了 `vpcConfig` 但关掉 `internetAccess` | B站下载 / 云端 ASR / DeepSeek 全部失败 | FC 支持「访问 VPC + 访问公网」并存，两个开关都要开（§4.2） |
| 误以为 `role` 管 ACR 拉取 / VPC 通路 | 白建角色、甚至配错导致日志丢失 | 这两件事由 FC 服务关联角色 `AliyunServiceRoleForFC` 负责；`role` 只用于「函数代码访问其他云服务」（§4.3） |
| 内置 `*.fcapp.run` 域名仅用于测试 | 生产稳定性无保障 | 官方明确建议绑已备案自定义域名；上线前替换 `WORKER_URL` 与前端 `BACKEND_ORIGIN` |

### 8.3 S2 改造方向：独立函数触发

目标：让异步 ASR 不再依赖「响应后的后台任务」。

1. **拆成两个函数**
   - `api` 函数（本文镜像）：只处理解析、总结、字幕快路径；`POST /api/transcribe` 改为**写任务 + 投递消息**后立即返回。
   - `worker` 函数（同一镜像，另加入口/命令）：消费队列，执行下载 + ASR，写回 Redis 任务状态。
2. **触发方式（已选定）**
   - **HTTP 触发器 + 请求级异步**（P3 采用）：`api` 用 `httpx` POST 到 worker 的触发器地址并带
     `X-Fc-Invocation-Type: Async`，FC 立即回 202 并落内部队列。**不需要 ACS3 签名、不需要 AK/SK、不需要 SDK**；
   - 备选：FC **OpenAPI 异步调用**（`InvokeFunction` + 签名）——需要 RAM 授权与常驻 AK/SK，仅当不允许配触发器时考虑；
   - 规模化后：**MNS / RocketMQ 队列触发**（尚未实现），天然带重试、削峰与死信队列。
3. **状态与幂等**：任务状态继续放 Redis（`TASK_STORE=redis` 已具备），worker 按 `taskId` 幂等更新；补 TTL 与失败重试上限。
   > **S2-P0 已完成**：`TASK_TTL_SECONDS`（默认 7 天滑动续期）、`TASK_INIT_MODE=none`（Worker 启动不改写状态）、`attempts` 字段、进度心跳（`TASK_HEARTBEAT_SECONDS`）与写入节流、同步桥接连接泄漏修复。
   > **S2-P1 已完成**：`services/dispatcher.py` + `TranscribeJob`（完整作业体，可 `to_dict()` 作 FC 事件 payload）+ `ENQUEUE_MODE=inline|fc|mns|redis`（默认 inline，行为不变；未实现模式显式报错）。
   > **S2-P2 已完成**：`app/worker_app.py` 独立 ASGI 应用（`POST /internal/worker/transcribe`）——不挂限流/CORS、不 import `app.main`，启动 `init(recover_interrupted=False)`；`SET task:lock:{id} NX EX` 幂等领取 + 心跳续期 + WATCH 比对释放；pipeline 经 `asyncio.to_thread` 执行（同步实现不能直接 await）；业务失败回 200、基础设施异常回 5xx。
   > **S2-P3 已完成**：`ENQUEUE_MODE=fc` 真正可用——`FcDispatcher` 用 `httpx` 把 `TranscribeJob` POST 到 worker 函数的 **HTTP 触发器**，带 `X-Fc-Invocation-Type: Async`（FC 立即回 202 并落内部队列）。**不签名、不引 SDK、不常驻 AK/SK**；`WORKER_TOKEN` 双向校验；worker 新增 `POST /invoke` 别名（`InvokeFunction` 兼容位）；`s.yaml` 双函数模板入库。
   > **S2-P4 已完成**：worker 函数加定时触发器（`reapTimer`，6 段 cron，每 5 分钟）→ FC 事件调用 → 容器 `/invoke` → 按 `triggerTime`/`triggerName` 特征分流到 `app/services/reaper.py`；`TASK_TIMEOUT` 错误码落地；`REAPER_LOCK_SECONDS` 互斥锁防多实例并发扫描。
   > **S2-Deploy 已完成**：`s.yaml` 补 `vpcConfig`（两函数同一套 VPC/交换机/安全组，访问内网 Redis）与 `role`（默认留空走 FC 服务关联角色）；文档补完整模板、网络/角色准备与「控制台全链路验证」流程。
   > 仍待实现：MNS / Redis 队列模式。
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
`ENQUEUE_MODE=fc`、`WORKER_URL`。完整对照见 §6.2 与仓库根目录 `s.yaml`。

### 8.4 P3 遗留与真机待验证项

- **R1（待真机确认）异步调用是否原样透传 path**：官方只写明「将请求持久化保存，然后立即返回响应」，没有一句明确说异步重放时 path 保持不变。同步透传模式是明确的，异步极可能一致。§7.3 第 1 步的 `curl` 就是专门验证这一点——若返回 404，把 `WORKER_PATH` 改成实际收到的路径即可（代码侧零改动）。
- **R2（已确认）异步 Body 上限 128 KB**：`TranscribeJob.to_dict()` 仅几百字节，余量充足。**不要往事件体里塞文字稿**——P1 只传作业入参的设计已规避。
- **R3（已由 P4 关闭）崩溃恢复**：worker 被 OOM/超时杀掉后，FC 的 3 次重试（间隔 0.5/1/2 秒）都会撞上 `TASK_LOCK_SECONDS=3600` 的锁并返回 `skipped`。现在由 worker 函数上的定时触发器（`reapTimer`，每 5 分钟）兜底：扫描 `status=processing` 且 `now - updated_at > TASK_STALE_SECONDS`（默认 600s）的任务，标 `failed` + `TASK_TIMEOUT`（「任务处理超时，可能因服务器资源受限中断，请重试」）。验收见 §7.3.1。
- **R7（P4 新增，已知行为）误杀后任务可能「复活」**：如果 worker 其实活着但心跳连续断了 20 次（>600s，概率极低），任务会被判 `failed`。**P1 已收窄**：心跳的 `update()` 走 WATCH/MULTI CAS，读到 `status != processing` 就放弃写入，不会把 `failed` 覆盖回 `processing`；`_finish` 的终态写入是显式的，仍可能写回 `completed`——这是可接受的，用户最多先看到一次错误提示，实际结果仍会落库。
- **R8（P4 新增，已知限制）Reaper 是全量扫描**：`status` 存在任务 JSON 体内，Redis 侧无法服务端过滤，每轮 `SCAN task:*` 要反序列化全部未过期任务（7 天 TTL 下可能上千 key）。当前量级完全够用；规模化后建议加 `ZSET task:processing`（score=updated_at）索引。**P4 不主动删 `task:lock:*`**：任务已 `failed`，重投会在 `_run_job` 首个状态检查返回 `skipped`；强删锁会破坏「release 必须带 token」的不变量，若 worker 还活着会导致同一任务被跑第二遍。

在此之前，请不要把 FC 版本用于「无字幕视频」的生产流量——除非已切到 `ENQUEUE_MODE=fc` 并完成 §7.3 验收。

---

## 9. 故障排查

| 现象 | 常见原因 | 处理 |
| --- | --- | --- |
| ACR 构建失败：`failed to resolve source metadata for docker.io/library/python` / `dial tcp 31.13.71.19:443: i/o timeout`（`[build failed, takes 30s]`） | 国内构建机连不上 Docker Hub，拉不到 `FROM` 的基础镜像。**与你的 Dockerfile 无关**（同一份 Dockerfile 本地构建可通过） | ACR 构建规则里**打开「海外机器构建」**再构建一次 |
| ACR 构建日志末尾的镜像路径与 `s.yaml` 不一致（如 `.../vtt-space1/vtt-backend:v1` vs 期望值） | 构建规则加在了另一个仓库上，或 `namespace` / `repo` 拼错 | 让构建目标的 `<命名空间>/<仓库名>` 与 `s.yaml` 的 `vars.namespace` / `vars.repo` 逐字符一致。**构建成功不代表部署能找到镜像** |
| `REPO_NOT_EXIST` / `Image not stored in ACR is not supported yet` | ACR 里还没有这个仓库或没有这个 tag（`--skip-push` 跳过了存在性检查，所以直到 `s deploy` 末尾才暴露） | 先确认 `s.yaml` 的 `vars.acr` 与 ACR 仓库页的「公网地址」域名一致（老版个人版是 `registry.<region>.aliyuncs.com`，新版是 `crpi-<id>.<region>.personal.cr.aliyuncs.com`）；再按 §3.1 建仓库并「立即构建」出该 tag。注意 `构建上下文目录` 填 `backend` |
| 启动失败：`exec format error` | 镜像不是 amd64 | 重新用 `--platform linux/amd64` 构建并校验 `docker image inspect` |
| 拉取镜像失败 / `ImagePullBackOff` | ACR 地址错、无权限、跨地域、VPC 未打通 | 检查镜像地址与 tag；配置免密拉取或 `registryConfig`；企业版 VPC 地址需配 `vpcConfig` |
| 请求 502 / 健康检查失败 | 端口不一致；uvicorn 未监听 `0.0.0.0`；启动即崩 | 对齐 `port` 与 `--port`；看启动日志；本地 `docker run` 复现 |
| 请求超时 / 504 | `timeout` 太小（长视频总结远超 30-70s） | 调到 300s |
| 任务一直 `processing` | `ENQUEUE_MODE=inline` 下 BackgroundTasks 被冻结；或 `fc` 模式投递失败 | 云端切 `ENQUEUE_MODE=fc`（§5、§7.3）；查 api 日志的 `FC 投递被拒` / `FC 投递失败` |
| api 日志 `FC 投递被拒（HTTP 404）` | `WORKER_URL` 少了路径，或异步未透传 path | 检查 `WORKER_URL` + `WORKER_PATH`；参考 §8.4 R1 |
| api 日志 `FC 投递被拒（HTTP 401）` | `WORKER_TOKEN` 两侧不一致 | 让 api 与 worker 用同一个 `WORKER_TOKEN` |
| api 日志 `FC 投递被拒（HTTP 403）` | 触发器认证方式不是 `anonymous` | 改触发器认证方式，或改用 Bearer/签名 |
| 任务 `failed`（「任务分发失败，请重试」） | `WORKER_URL` 漏配 / 写错 / FC 拒收 | 见 §5.2 两段式部署 |
| 任务一直 `processing`，超过 15 分钟仍未变 `failed` | 定时触发器没生效（S2-P4） | `s info` 查 worker 的 `reapTimer`；手工 `curl /internal/worker/reap` 验逻辑；见 §7.3.1 |
| worker 日志 `收到定时触发器事件` 但随后 422 | `/invoke` 分流未命中 | 确认 event 含 `triggerTime` + `triggerName`；见 §7.3.1 判读表 |
| 推送了新镜像、`s deploy` 也成功，但函数行为没变 | **ACR 代码源自动构建覆盖了同一个 tag** | 用 `docker buildx imagetools inspect <image>:<tag>` 看 registry 上的 digest，与 `s info` 的 `resolvedImageUri` 对比。根因见 §3.1（自动构建与手工推送共用 `:v1`）；关掉自动构建，或让自动构建用独立 tag |
| 定时触发器每 5 分钟一条 `POST /invoke 401` | `reapTimer.payload` 里的 token 与 `WORKER_TOKEN` 不一致（改过变量但没重新部署，或 payload 漏配 token） | `s info` 查触发器 payload；重新 `s deploy` 让 `${env('WORKER_TOKEN')}` 重新渲染；见 §7.3.1 |
| 任务 `failed`（「任务处理超时…」） | reaper 判定心跳超时（正常行为） | 用户点重试即可；若频繁出现说明实例被 OOM/超时杀，查 worker 规格与 `timeout` |
| 429 `RATE_LIMITED` | 触发限流 | 调 `RATE_LIMIT_PER_MINUTE`；确认 Redis 生效（否则各实例各自计数） |
| `CLOUD_NOT_CONFIGURED` | 未注入 `CLOUD_ASR_API_KEY` | 检查环境变量与 `s info` |
| `SUMMARIZE_NOT_CONFIGURED` | 未注入 `DEEPSEEK_API_KEY` | 同上；qwen 用 `DASHSCOPE_API_KEY` |
| 日志出现 Redis 降级警告 | `REDIS_URL` 不可达 / 密码错 / 未配 VPC | 用 VPC 内网地址 + 安全组放行；确认 `redis` 包在镜像内（`requirements-cloud.txt` 已含） |
| `AUDIO_TOO_LARGE` | 超过 `MAX_AUDIO_SIZE_MB` | 调大上限并同步调大内存，或降低上限优雅拒绝 |
| CORS 报错（仅直连调试时） | `CORS_ORIGINS` 未含来源 | 加域名；生产走 Next 代理时通常不会触发 |
| 解析 403 / 412 | B站风控（设备指纹 / Cookie / UA 变化） | 已内置设备指纹自动获取；仍失败时先 `POST /internal/worker/diagnose` 看 `baseline` vs `hardened` 对比，再决定配置 `BILI_USER_AGENT` / `BILI_COOKIE` |

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
