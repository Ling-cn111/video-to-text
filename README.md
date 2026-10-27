# video-to-text

基于 Serverless 架构的视频转文字与 AI 总结工具。粘贴一个 B站链接，从几秒（命中字幕）到几分钟（走 ASR）拿到带时间戳的文字稿与结构化 AI 摘要。

- **零成本字幕快路径**：视频有 CC / AI 字幕时直接取，27 分钟视频 5-7 秒出稿，¥0
- **分层降级流水线**：字幕快路径（秒级 ¥0）→ 语音识别（云端优先、本机自建可走 faster-whisper）→ LLM 总结，逐层兜底不空手
- **真实 AI 结构化总结**：一句话概要 / 带时间戳要点 / 章节笔记，时间戳锚定原文
- **两种安装形态**：Web PWA（配好可达域名后可装到主屏幕）、Windows 桌面安装包（Tauri，不依赖域名可达性）
- **深色模式与自适阅读**：浅色 / 深色 / 跟随系统三档；全文可调字号、搜索高亮、按章节跳转
- **自带 API Key（BYOK，可自由换服务商）**：右上角「API 设置」可填自己的 **Key + 接口地址 + 模型名**，任何兼容 OpenAI `chat/completions` 的服务商都能用（DeepSeek / 百炼 / Moonshot / 智谱 / 硅基流动 / OpenAI / 自建中转），用各自的额度、不占站点配额；Key 只存在本机浏览器，生成总结时随请求头发给后端，不进请求体、不落库、不写日志。语音识别用的是免费模型，不需要填 Key
- **直接输入视频号**：`BV1xx411c7mD`、`av170001` 这类裸号可以直接粘进首页输入框，不必凑出完整链接

| | 入口 |
| --- | --- |
| 在线体验 | <https://video-to-text-six.vercel.app> |
| 桌面端下载（Windows） | [**v0.1.2 · .exe 直链**](https://github.com/Ling-cn111/video-to-text/releases/download/v0.1.2/video-to-text_0.1.2_x64-setup.exe) ／ [全部 Release](https://github.com/Ling-cn111/video-to-text/releases/latest) |
| 手机端 | **暂未提供安装包**，见下方说明 |

### 桌面端下载

当前版本 **v0.1.2**，点文件名直接下载（无需先进 Release 页找）：

- [**video-to-text_0.1.2_x64-setup.exe**](https://github.com/Ling-cn111/video-to-text/releases/download/v0.1.2/video-to-text_0.1.2_x64-setup.exe) — 2.3 MB，NSIS，普通用户双击安装，**无需管理员权限**（推荐）
- [**video-to-text_0.1.2_x64_zh-CN.msi**](https://github.com/Ling-cn111/video-to-text/releases/download/v0.1.2/video-to-text_0.1.2_x64_zh-CN.msi) — 3.3 MB，WiX，适合企业组策略 / 静默分发

校验和（`certutil -hashfile <文件> SHA256`）：

```
f079da153344430aab9b7fbe04903168f7aa3104bb5f61c1a2cadd6fa177d396  video-to-text_0.1.2_x64-setup.exe
5636dc96cd532e6dd3685fb313b9dc546f8f87d2d6ce1ebf3b9f727e9a0ffa8a  video-to-text_0.1.2_x64_zh-CN.msi
```

> **v0.1.1 及更早版本已失效**：站点不再内置 AI 总结 Key，旧版既没有「API 设置」入口、又始终向后端声明用本地转写引擎（线上镜像不含本地 ASR），装了之后总结永远灰着且没有出路。请升级到 v0.1.2。
> 安装包未做代码签名，Windows SmartScreen 会提示「未知发布者」，点「更多信息 → 仍要运行」即可。
> 安装后不依赖域名可达性：桌面端直接连后端 API，不受 `*.vercel.app` 被墙影响。
> `github.com` 在国内可能连不上，若下载失败需自备代理；网页端本身不受此影响。

### 手机端

**目前没有 Android / iOS 安装包。** 现状与可行路径：

| 方式 | 状态 |
| --- | --- |
| PWA 装到主屏幕 | 功能已实现，但 `*.vercel.app` 被 SNI 阻断，国内手机上 `beforeinstallprompt` 不会触发，**实际装不上** |
| 换自定义域名后装 PWA | 可行，域名解析后 PWA 即可安装（无需打包） |
| Capacitor 包壳上架 | **未启动**，见 [路线图 M4](docs/ROADMAP.md) |

也就是说：手机上目前只能通过浏览器访问网页使用（同样需要域名可达），没有可下载的 App。
如果你需要手机 App，最短路径是给网页配一个自定义域名（PWA 立即可装），而不是等打包。

构建方法见 [桌面端打包指南](docs/desktop-packaging.md)。

## 架构

```mermaid
flowchart LR
  C["客户端<br/>Web PWA / Tauri 桌面端"]
  A["阿里云 FC · API 函数<br/>FastAPI<br/>解析 + LLM 总结"]
  W["阿里云 FC · Worker 函数<br/>ASR 转写"]
  R[("Redis 任务存储<br/>阿里云 Redis / Upstash<br/>TTL 7 天")]
  X["外部服务<br/>B站 / 硅基流动 / DeepSeek"]

  C -->|"POST /api/parse"| A
  A -->|"命中字幕快路径，同步返回"| C
  C -->|"轮询 GET /api/transcribe/:id"| A
  C -->|"POST /api/summarize（文字稿已在手）"| A
  A -->|"异步派发（FC Invoke）"| W
  A <-->|"读写任务状态"| R
  W <-->|"读写任务状态"| R
  A -->|"B站页面 / LLM"| X
  W -->|"下载音频 / 云端 ASR"| X
```

**为什么必须异步**：ASR 要 30-155 秒（长视频更久），而 API 函数超时是 300 秒；更关键的是 FC 在 HTTP 响应返回后会**冻结/回收实例**，`BackgroundTasks` 挂的后台协程拿不到 CPU——超时配多大都没用。因此 API 只做「解析 + 派发」并立即返回 `taskId`，作业体投交给独立的 Worker 函数（超时 1800 秒、单实例并发 1），前端轮询状态。**LLM 总结留在 API 侧**（`/api/summarize`）：它只依赖已经落库的文字稿，不需要重新下载音频，放 API 可以直接复用同一实例处理完；Worker 因此只管 ASR 这一件重活。设计推演见 [异步架构侦察报告](docs/s2-async-recon.md)。

## 快速开始

### 桌面端

1. 取安装包（见上表，`dist/` 或自行构建），双击安装（无需管理员权限）
2. 从开始菜单打开「视频转文字」，粘贴 B站链接即可使用

### 本地开发（Web）

```bash
# 前端
pnpm install
cp .env.example .env.local     # 见下表，至少确认 NEXT_PUBLIC_USE_MOCK
pnpm dev
```

```bash
# 后端（另开终端）
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

打开 <http://localhost:3000>。首页提供示例链接，含失败与不支持平台的演示。

最核心的三个变量（全表见 [`.env.example`](.env.example)）：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `NEXT_PUBLIC_USE_MOCK` | `true` | **构建期常量**。`false` 时走真实后端（经 `/backend-api/*` 同源代理） |
| `BACKEND_ORIGIN` | `http://localhost:8000` | 服务端变量，仅 Next rewrites 使用，不暴露给浏览器 |
| `NEXT_PUBLIC_API_BASE` | 空 | 桌面端专用，静态导出后跨域直连后端的绝对地址 |

后端另需在 `backend/.env.local` 配 `CLOUD_ASR_API_KEY`（云端转写，硅基流动的 ASR 模型免费，Key 仍需免费注册）与 `DEEPSEEK_API_KEY`（AI 总结），两者均可选。**AI 总结 Key 建议不配**：本站设计上让使用者填自己的 Key（右上角「API 设置」），站点不配时总结区会直接引导填写；未配置的对应功能会明确提示。

> **Windows 用户**：双击 `setup.bat` 一键装好全部依赖，再用 `控制台.bat` 启动托盘控制台。细节见 [本地开发指南](docs/local-development.md)。

## 核心逻辑

### 四条路径与成本

| 路径 | 触发条件 | 耗时（27 分钟视频） | 成本 | 质量 |
| --- | --- | --- | --- | --- |
| ① 字幕快路径（**主路径**） | B站视频有 CC / AI 字幕 | **5-7 秒** | ¥0 | B站官方字幕，接近人工 |
| ② 云端 ASR（自动兜底） | 无字幕，站点或请求头有云端 ASR Key | 19-43 秒（150s 音频） | ¥0（硅基流动 ASR 免费） | 4-17% CER（绝对基准） |
| ③ 本地 ASR（自建部署可选） | 无字幕，且后端装了 faster-whisper | 36-155 秒（150s 音频） | ¥0 | 清晰口播 6-22% CER，喊叫类 44% |
| ④ LLM 总结 | 转写完成后自动触发（需可用 LLM Key） | 33-69 秒 | **¥0.07 / 27 分钟视频**（唯一成本项） | 结构化概要 / 要点 / 章节 |

前三层是降级关系（有字幕就不跑 ASR），走哪条由后端按实际可用性决定：线上镜像只装云端 ASR，
所以永远是 ①→②；本地自建装好 faster-whisper 后多一层 ③。数据来源：[ASR 基准](docs/asr-benchmark.md) / [LLM 成本记录](docs/llm-cost.md)。

### 两条开发铁律

- **任务页 URL 只能由 [`src/lib/routes.ts`](src/lib/routes.ts) 生成**（`resultHref()` / `processingHref()`）。手拼 `/result/${id}` 在桌面端会 404，因为静态导出改用查询参数形态。
- **导出与「全文阅读」同源且零时间戳**。TXT / MD 必须与页面全文视图逐段一致，改任一侧都要同步另一侧。

## 项目结构

```
backend/                  # FastAPI + yt-dlp 服务（详见 backend/README.md）
└── app/                  # parse / url_guard(SSRF 守卫) / platforms / services/
src/
├── app/                  # 路由与 Mock API（/、/processing?id=、/result?id=、/api/*）
├── components/           # home / processing / result / shared / layout / pwa / ui
├── lib/                  # types(契约) api(出口) real-api http(请求层) mock-*
│                         # platforms(平台注册表) article(分段) export(导出) format
├── stores/task-store.ts  # Zustand 任务状态机
└── hooks/                # 轮询等
e2e/                      # Playwright 功能锁定用例
src-tauri/                # Tauri 桌面端壳
```

技术栈：Next.js 14（App Router）+ TypeScript + Tailwind + shadcn/ui + Zustand；后端 FastAPI + yt-dlp + faster-whisper + DeepSeek/Qwen；PWA 用 Web App Manifest + Service Worker（仅生产环境注册）。

## 部署

**前端（Vercel）**：GitHub 导入仓库即可，框架自动识别 Next.js。仅需两个环境变量——`BACKEND_ORIGIN`（后端地址）与 `NEXT_PUBLIC_USE_MOCK=false`，两者均为**构建期生效**，事后修改必须重新部署。

**后端（阿里云 FC）**：容器镜像 + `s.yaml` 双函数（API / Worker），完整流程见 [serverless-deploy.md](docs/serverless-deploy.md)。

## 文档索引

| 想了解 | 去看 |
| --- | --- |
| 后端 FC 部署全流程（镜像构建、`s.yaml`、VPC、变量、验收） | [docs/serverless-deploy.md](docs/serverless-deploy.md) |
| 本地开发与日常运行（Windows 脚本、托盘、故障排查） | [docs/local-development.md](docs/local-development.md) |
| 测试指南（lint / build / E2E / pytest） | [docs/testing.md](docs/testing.md) |
| 前端功能锁定协议与回归清单 | [docs/frontend-features.md](docs/frontend-features.md) |
| API 契约（接口 / 错误码 / 路由约定） | [docs/api-contract.md](docs/api-contract.md) |
| 桌面端打包指南（WiX / NSIS / 国内镜像） | [docs/desktop-packaging.md](docs/desktop-packaging.md) |
| ASR 准确率评测基准（绝对 CER） | [docs/asr-benchmark.md](docs/asr-benchmark.md) |
| 异步架构改造侦察报告 | [docs/s2-async-recon.md](docs/s2-async-recon.md) |
| LLM 成本记录 | [docs/llm-cost.md](docs/llm-cost.md) |
| 后端 API / 环境变量 / 容器化 | [backend/README.md](backend/README.md) |
| 路线图与阶段验收 | [ROADMAP](docs/ROADMAP.md) · [阶段二](docs/acceptance-stage-2.md) · [阶段三](docs/acceptance-stage-3.md) |

## 已知限制

- **仅支持 B站**，抖音解析目前只在前端 Mock 中预置，真实后端待接入
- B站 AI 字幕非全覆盖（实测 6 个视频中 5 个有轨道），无字幕时走 ASR 兜底
- 本地 ASR 依赖 CPU：small 模型处理 150 秒音频约需 40-155 秒
- **云端镜像不含本地 ASR**（`backend/requirements-cloud.txt` 刻意排除 faster-whisper / torch），所以网页端与桌面端只有「字幕快路径 → 云端 ASR」两级；`ASR_ENGINE_FALLBACK=local` 在线上是空转，代码里已加探测（`local_engine_available()`）避免白等
- **云端 ASR 的免费是「刊例价 0 元」**（硅基流动 `free-asr-model` 系列，按千字符计），不排除服务商后续调价；换模型时请重新核对价格页
- 云端部署时若 `REDIS_URL` 连不上，应用会静默降级到内存存储，表现为任务永远停在 `processing`
- PWA 在国内手机暂不可安装：`*.vercel.app` 被 DNS 污染与 SNI 阻断，需换自定义域名
- 桌面端安装包未做代码签名，SmartScreen 会提示「未知发布者」；也**没有自动更新**，升级需重新下载安装
- 手机端无原生安装包（Capacitor 包壳未启动），仅能通过浏览器访问网页

## 协作规范

- 分支：`main`（已开分支保护：禁止强推与删除，三组 CI 必须通过才能合并）← 功能分支，一个功能一个 PR（squash 合并）
- 提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)（`feat:` / `fix:` / `chore:` / `docs:` / `ci:`）
- PR 会自动跑三组检查（[CI 配置](.github/workflows/ci.yml)）：`lint + build`、`backend tests`、`e2e locked features`。这三项是 GitHub 分支保护里的必需检查，未通过时合并按钮是灰的
- 改前端前请先读 [前端功能锁定协议](docs/frontend-features.md)，模板位于 `.github/`
