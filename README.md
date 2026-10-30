# video-to-text

基于 Serverless 架构的视频转文字与 AI 总结工具。粘贴一个 B站链接，从几秒（命中字幕）到几分钟（走 ASR）拿到带时间戳的文字稿与结构化 AI 摘要。

- **零成本字幕快路径**：视频有 CC / AI 字幕时直接取，27 分钟视频 5-7 秒出稿，¥0
- **分层降级流水线**：字幕快路径（秒级 ¥0）→ 语音识别（云端优先、本机自建可走 faster-whisper）→ LLM 总结，逐层兜底不空手
- **真实 AI 结构化总结**：一句话概要 / 带时间戳要点 / 章节笔记，时间戳锚定原文；三档详细度（精简 / 标准 / 详细）可随时切换，切换会重新生成而不是裁剪旧结果
- **两种安装形态**：Web PWA（配好可达域名后可装到主屏幕）、Windows 桌面安装包（Tauri，不依赖域名可达性）；Android 另可打包 APK（Capacitor，同样不依赖域名）
- **手机上也能导出**：App 内点导出会拉起系统分享面板（存到文件 / 发给微信），不再依赖浏览器下载——`blob:` 下载在 WebView 里根本不会落地
- **深色模式与自适阅读**：浅色 / 深色 / 跟随系统三档；全文可调字号、搜索高亮、按章节跳转
- **字幕文件导出**：除阅读用的 TXT / Markdown（零时间戳）外，还可导出 SRT / VTT 字幕，直接挂到播放器；两类产物语义相反，入口分组、绝不混用
- **最近转写**：首页记住最近 8 条任务，关掉标签页也能找回；转写期间误关会先确认一次
- **进度页给的是真实反馈**：当前阶段剩余时间预估、按阶段自动退避的轮询（10 秒内 800ms → 之后 3s）、切到后台暂停轮询
- **自带 API Key（BYOK，可自由换服务商）**：右上角「API 设置」可填自己的 **Key + 接口地址 + 模型名**，任何兼容 OpenAI `chat/completions` 的服务商都能用（DeepSeek / 百炼 / Moonshot / 智谱 / 硅基流动 / OpenAI / 自建中转），用各自的额度、不占站点配额；Key 只存在本机浏览器，生成总结时随请求头发给后端，不进请求体、不落库、不写日志。语音识别用的是免费模型，不需要填 Key
- **直接输入视频号**：`BV1xx411c7mD`、`av170001` 这类裸号可以直接粘进首页输入框，不必凑出完整链接
- **限流有明确说法**：触发 429 时显示「请求太频繁」并给出冷却倒计时，冷却期内禁用重试（重试本身也是 POST，必然再撞限流）

| | 入口 |
| --- | --- |
| 在线体验 | <https://video-to-text-six.vercel.app> |
| 桌面端下载（Windows） | [**v0.1.3 · .exe 直链**](https://github.com/Ling-cn111/video-to-text/releases/download/v0.1.3/video-to-text_0.1.3_x64-setup.exe) ／ [全部 Release](https://github.com/Ling-cn111/video-to-text/releases/latest) |
| 手机端 | **Android 包壳已打通**（Capacitor，源码内可直接构建 APK），尚未发布安装包，见下方说明 |

### 桌面端下载

当前版本 **v0.1.3**，点文件名直接下载（无需先进 Release 页找）：

- [**video-to-text_0.1.3_x64-setup.exe**](https://github.com/Ling-cn111/video-to-text/releases/download/v0.1.3/video-to-text_0.1.3_x64-setup.exe) — 2.3 MB，NSIS，普通用户双击安装，**无需管理员权限**（推荐）
- [**video-to-text_0.1.3_x64_zh-CN.msi**](https://github.com/Ling-cn111/video-to-text/releases/download/v0.1.3/video-to-text_0.1.3_x64_zh-CN.msi) — 3.3 MB，WiX，适合企业组策略 / 静默分发

校验和（`certutil -hashfile <文件> SHA256`）：

```
c2dcc07de48c2844c8312ba13313d7856275b15735171c55126ce8d11c9256e3  video-to-text_0.1.3_x64-setup.exe
7ec01b6bc8761bd3c60799723ec679c9e70d39055a15f4202c56324ab1ca439e  video-to-text_0.1.3_x64_zh-CN.msi
```

> **请使用 v0.1.3**：它支持自由配置 AI 总结服务商（接口地址 + 模型名 + Key）与直接输入 BV / av 号。v0.1.2 只能填 Key、服务商被钉死在站点预设的那一两家，填别家的 Key 会被误报「Key 无效」；v0.1.1 及更早版本更严重——没有「API 设置」入口，又始终向后端声明用本地转写引擎（线上镜像不含本地 ASR），装了之后总结永远灰着且没有出路。
> 安装包未做代码签名，Windows SmartScreen 会提示「未知发布者」，点「更多信息 → 仍要运行」即可。
> 安装后不依赖域名可达性：桌面端直接连后端 API，不受 `*.vercel.app` 被墙影响。
> `github.com` 在国内可能连不上，若下载失败需自备代理；网页端本身不受此影响。

### 手机端（Android）

**包壳已打通，可自行构建 APK；尚未发布安装包。** 现状：

| 方式 | 状态 |
| --- | --- |
| Android APK（Capacitor 包壳） | **已实现** ✅ 前端资源打进安装包，直连后端 API，**不依赖域名可达性**。构建见下方命令 |
| PWA 装到主屏幕（Android） | 功能已实现，但 `*.vercel.app` 被 SNI 阻断，国内手机上 `beforeinstallprompt` 不会触发，**实际装不上** |
| PWA 装到主屏幕（iOS） | 即使域名可达也**不会出现「安装应用」按钮**：iOS Safari 从不触发 `beforeinstallprompt`，顶栏入口因此不显示。需手动走「分享 → 添加到主屏幕」 |
| 换自定义域名后装 PWA | 可行，域名解析后 Android 的安装按钮即刻出现（无需打包）；iOS 仍需上面的手动步骤 |
| iOS 包壳 | **未启动**：打包必须有 macOS + Xcode，当前开发机是 Windows |

也就是说：Android 已经可以打包成独立 App（不需要域名）；iOS 目前只能通过浏览器访问网页。

**自行构建 Android APK**（需要 JDK 17+ 与 Android SDK，无需 Android Studio）：

```bash
pnpm install
pnpm build:mobile                 # 静态导出到 out/，注入 NEXT_PUBLIC_USE_MOCK=false
npx cap sync android              # 把 out/ 复制进 android/app/src/main/assets/public/
cd android
./gradlew --init-script gradle/init-mirrors.gradle assembleDebug   # 产物：app/build/outputs/apk/debug/
```

发布版签名：把 `android/keystore.properties.example` 复制成 `keystore.properties` 并填入 keystore 路径与密码，然后 `assembleRelease`。**keystore 必须备份**——丢了之后已安装用户永远无法升级。

> **`--init-script gradle/init-mirrors.gradle` 不能省**：Capacitor 子工程的 `build.gradle` 把 `google()` 排在最前，
> 直连 `dl.google.com` 在国内会卡十几分钟再超时失败。这个 init script 在子工程脚本求值前插入阿里云镜像。

> 手机上装的是**本地资源**，所以 `*.vercel.app` 被墙不影响 App 使用；只有 API 请求需要联网（后端在阿里云 FC，可达）。

构建方法见 [桌面端打包指南](docs/desktop-packaging.md)（静态导出与后端地址注入的共用逻辑）。

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

1. 从上表下载当前版本安装包，双击安装（无需管理员权限）
2. 从开始菜单打开「视频转文字」，粘贴 B站链接即可使用

> 自行构建的话，产物在 `src-tauri/target/release/bundle/`（`msi/` 与 `nsis/` 两个子目录），
> 不在仓库根的 `dist/`。分发请以 [Release 资产](https://github.com/Ling-cn111/video-to-text/releases/latest) 为准，
> 不要拿本地历史遗留的旧包——版本对不上时用户装到的是已经修复过的故障。

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
| Android 包壳的不变量与踩坑 | [docs/frontend-features.md](docs/frontend-features.md) 第 16 节 |
| ASR 准确率评测基准（绝对 CER） | [docs/asr-benchmark.md](docs/asr-benchmark.md) |
| 异步架构改造侦察报告 | [docs/s2-async-recon.md](docs/s2-async-recon.md) |
| LLM 成本记录 | [docs/llm-cost.md](docs/llm-cost.md) |
| 后端 API / 环境变量 / 容器化 | [backend/README.md](backend/README.md) |
| 路线图与阶段验收 | [ROADMAP](docs/ROADMAP.md) · [阶段二](docs/acceptance-stage-2.md) · [阶段三](docs/acceptance-stage-3.md) |

## 已知限制

- **仅支持 B站**，抖音解析目前只在前端 Mock 中预置，真实后端待接入
- B站 AI 字幕非全覆盖（实测 6 个视频中 5 个有轨道），无字幕时走 ASR 兜底
- 本地 ASR 依赖 CPU：small 模型处理 150 秒音频约需 36-155 秒（含冷启动，见 [ASR 基准](docs/asr-benchmark.md)）
- **云端镜像不含本地 ASR**（`backend/requirements-cloud.txt` 刻意排除 faster-whisper / torch），所以网页端与桌面端只有「字幕快路径 → 云端 ASR」两级；`ASR_ENGINE_FALLBACK=local` 在线上是空转，代码里已加探测（`local_engine_available()`）避免白等
- **云端 ASR 的免费是「刊例价 0 元」**（硅基流动 `free-asr-model` 系列，按千字符计），不排除服务商后续调价；换模型时请重新核对价格页
- 云端部署时若 `REDIS_URL` 连不上，应用会静默降级到内存存储，表现为任务永远停在 `processing`
- PWA 在国内手机暂不可安装：`*.vercel.app` 被 DNS 污染与 SNI 阻断，需换自定义域名
- 桌面端安装包未做代码签名，SmartScreen 会提示「未知发布者」；也**没有自动更新**，升级需重新下载安装
- **Android 安装包尚未发布**：包壳与签名配置都已就位（`pnpm build:mobile` 可自行构建），但没有走分发流程；iOS 需 macOS + Xcode，未启动
- Android 包的最低系统要求是 **Android 7.0（API 24）**，targetSdk 36；打包需 JDK 17+ 与 Android SDK（无需 Android Studio）

## 协作规范

- 分支：`main`（已开分支保护：禁止强推与删除，三组 CI 必须通过才能合并）← 功能分支，一个功能一个 PR（squash 合并）
- 提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)（`feat:` / `fix:` / `chore:` / `docs:` / `ci:`）
- PR 会自动跑三组检查（[CI 配置](.github/workflows/ci.yml)）：`lint + build`、`backend tests`、`e2e locked features`。这三项是 GitHub 分支保护里的必需检查，未通过时合并按钮是灰的
- 改前端前请先读 [前端功能锁定协议](docs/frontend-features.md)，模板位于 `.github/`
