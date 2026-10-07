# video-to-text

视频链接转文字 PWA：粘贴 B站视频链接，自动完成解析、转写（B站字幕快路径优先，否则本地/云端 ASR）与真实 AI 总结（DeepSeek），输出带时间戳的文字稿和结构化摘要。可安装到 Windows 桌面与手机主屏幕。

> **当前进度**：阶段一（真实解析）✅ → 阶段二（真实转写 + 绝对 CER 基准）✅ → **阶段三（真实 AI 总结）✅（2026-10-07）**。多平台 / 本地视频 / 打包 App 规划见 [docs/ROADMAP.md](docs/ROADMAP.md)；验收报告 [阶段二](docs/acceptance-stage-2.md) / [阶段三](docs/acceptance-stage-3.md)。

## 转写与总结的四条路径（及成本）

| 路径 | 触发条件 | 耗时（27 分钟视频） | 成本 | 质量 |
| --- | --- | --- | --- | --- |
| ① 字幕快路径（**主路径**） | B站视频有 CC / AI 字幕（弹幕元数据接口未登录可取） | **5-7 秒** | ¥0 | B站官方字幕，接近人工 |
| ② 本地 ASR（默认兜底） | 无字幕 + 首页「转写模式=本地」 | 36-155 秒（150s 音频） | ¥0 | 清晰口播 6-22%（CER），喊叫类 44% |
| ③ 云端 ASR（手动选择） | 无字幕 + 强 BGM / 追求准确 | 19-43 秒（150s 音频） | ¥0（硅基流动 ASR 免费） | 4-17%（CER，绝对基准） |
| ④ LLM 总结（阶段三） | 转写完成后自动触发 | 33-69 秒 | **¥0.07 / 27 分钟视频**（唯一成本项） | 结构化概要/要点/章节，时间戳指向原文 |

数据来源：[绝对 CER 基准](docs/asr-benchmark.md) / [LLM 成本记录](docs/llm-cost.md)。

## 功能

- 粘贴 B站视频链接 → 真实解析（yt-dlp）→ 转写（四层降级：CC 字幕 → AI 字幕 → 本地/云端 ASR）→ 结果页
- 全文阅读：无时间戳的文章式排版（按章节自动分段，段间空行、行高舒适）
- 带时间戳文字稿（点击时间戳可复制，支持折叠/展开）；字幕来源任务显示「来源：B站字幕」Badge
- 右侧 AI 总结（真实 LLM 生成：一句话概要 / 带时间戳要点 / 章节笔记）
- 导出 TXT / Markdown（纯文本全文，不含时间戳）
- PWA：可安装、离线可用（应用外壳）
- 响应式：桌面左右分栏，移动端上下卡片流
- 错误态：无效链接、不支持平台、转写/总结失败（可重试）

## 支持的平台

| 平台 | 解析 | 转写 |
| --- | --- | --- |
| 哔哩哔哩（www.bilibili.com / b23.tv） | ✅ yt-dlp | ✅ 字幕快路径（CC + AI 字幕，未登录可取）/ 本地 faster-whisper / 云端 ASR |

> 更多平台在 `src/lib/platforms/registry.ts` 与 `backend/app/platforms.py` 的注册表中追加即可。

## 已知限制

- **停止服务后刷新页面仍显示界面？** 若你的浏览器曾访问过生产构建（或点过首页「安装」），PWA 的 Service Worker 会缓存**离线外壳**——服务停止后刷新会显示缓存的界面（这是「离线可用外壳」的设计行为），但页面内任何数据操作都会明确报「网络异常」。彻底清除：F12 → Application → Service Workers → Unregister，或在浏览器设置中清除该站点数据
- 转写任务已持久化到 SQLite（`backend/data/tasks.db`）：服务重启后历史任务可查，重启时未完成任务标记「已中断」并引导重试；多 worker / 云端部署仍需替换为 Redis（接口已封装，见 backend/README.md）
- 当前仅支持 B站；抖音解析在前端 Mock 中预置，真实后端待接入
- B站 AI 字幕非全覆盖（实测 6 视频中 5 个有轨道），无字幕时走 ASR 兜底
- LLM 总结需配置 `DEEPSEEK_API_KEY`（或通义 `DASHSCOPE_API_KEY`），未配置时总结步骤明确报错
- 本地 ASR 依赖 CPU：small 模型 150 秒音频约 40-155 秒；FFmpeg 可选（未装自动降级 PyAV 解码）

## 技术栈

- [Next.js](https://nextjs.org) 14（App Router）+ TypeScript
- Tailwind CSS + [shadcn/ui](https://ui.shadcn.com)
- Zustand 状态管理
- Next Route Handlers 提供 Mock API（`src/app/api`），客户端统一出口 `src/lib/api.ts`
- 后端：FastAPI + yt-dlp + faster-whisper + DeepSeek/Qwen（见 [backend/README.md](backend/README.md)）
- PWA：Web App Manifest + Service Worker（仅生产环境注册）

## 快速开始（Windows）

### 首次配置

双击 `setup.bat`，自动创建虚拟环境、安装前后端依赖、复制配置模板（已存在的配置文件不会覆盖）。
按提示填写 `backend/.env.local` 中的 `DEEPSEEK_API_KEY`（AI 总结）与 `CLOUD_ASR_API_KEY`（云端转写，均可选）。

### 日常使用

**方式一：系统托盘控制台（推荐）**

- 启动：双击 `控制台.bat`——托盘区出现状态圆点，右键菜单：启动 / 停止 / 重启 / 查看日志 / 开机自启 / 退出；左键双击图标切换启停
- 图标状态：🟢 运行中 ｜ 🟡 启动中 ｜ ⚪ 已停止 ｜ 🔴 出错（气泡提示，日志见 `logs/`）
- 服务在后台隐藏运行，无黑窗口；启动完成自动打开浏览器
- 未安装快捷方式时也可右键 `控制台.ps1` →「使用 PowerShell 运行」
- **看不到图标？** Windows 11 默认把新出现的托盘图标收进折叠区：点击任务栏的 `^` 展开即可看到；可在「设置 → 个性化 → 任务栏 → 其他系统托盘图标」中把它设为常显
- 命令行等价：`powershell -ExecutionPolicy Bypass -File 控制台.ps1 -Action start|stop|restart|status`
- 托盘与 `run.bat` 共享端口 / 日志 / PID 约定，两者可互相启停

**方式二：命令行脚本（run.bat）**

- 启动：双击 `run.bat` 或命令行 `run.bat start`（前后端各开一个窗口，浏览器自动打开 http://localhost:3000）
- 关闭：`run.bat stop`（按 PID → 窗口标题 → 端口三层兜底）
- 重启：`run.bat restart`
- 查看状态：`run.bat status`（显示运行状态、PID 与最新日志文件）
- 查看用法：`run.bat help`（双击运行时如遇错误会停留等待按键，便于查看提示）

后端 / 前端日志写入 `logs/backend-*.log` 与 `logs/frontend-*.log`（自动轮转，各保留 20 份）；
进程 PID 记录在 `.vtt-pids/`。以上目录与数据库 `backend/data/` 均不入库。

## 手动启动（Mac/Linux 或调试用）

```bash
# 前端
pnpm install
pnpm dev      # 开发模式（不注册 Service Worker）
```

```bash
# 后端（另开终端）
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows（macOS/Linux: source .venv/bin/activate）
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

打开 <http://localhost:3000>，使用首页提供的示例链接体验完整流程（含失败 / 不支持平台演示）。
后端自检：<http://localhost:8000/api/health>；接口文档：<http://localhost:8000/docs>。

```bash
pnpm lint     # 代码检查
pnpm build    # 生产构建
pnpm start    # 生产模式（注册 Service Worker，可验证 PWA）
```

### 图标生成（PWA 资源）

```bash
pnpm node scripts/generate-icons.mjs   # 修改 scripts/generate-icons.mjs 后重新生成 public/icons/
```

## 项目结构

```
backend/                # FastAPI + yt-dlp 真实解析服务（详见 backend/README.md）
└── app/                # parse 路由 / url_guard(SSRF守卫) / platforms(平台注册表) / services/parser(yt-dlp)
src/
├── app/                  # 路由与 API（/、/processing/[taskId]、/result/[taskId]、/api/*）
├── components/           # home / processing / result / shared / layout / pwa / ui(shadcn)
├── lib/                  # types(契约) api(出口) real-api(真实后端) http(共享请求层)
│                         # mock-api(无状态Mock) mock-data(假数据)
│                         # platforms/registry(平台注册表) platform(链接解析) url-guard(安全校验)
│                         # article(全文分段) export(导出) format(格式化)
├── stores/task-store.ts  # Zustand 任务状态机
└── hooks/                # 轮询等
```

## 本地联调真实后端（当前仅「解析」为真实实现）

```bash
# 1) 后端（Python 3.11+）
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 2) 前端（等价于 npm run dev）
cp .env.example .env.local   # 把 NEXT_PUBLIC_USE_MOCK 改为 false
pnpm dev
```

粘贴 B站链接，进度页 / 结果页即显示真实标题、封面、时长（转写与总结仍为内置 Mock）。

## API 契约

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/parse` | 解析视频链接 → `{ title, cover, duration, platform, videoId }` |
| POST | `/api/transcribe` | 创建转写任务 → `{ taskId, status, progress, stage }` |
| GET | `/api/transcribe/:taskId` | 轮询任务 → `{ status, stage, progress, video?, transcript?, plainText?, error? }` |
| POST | `/api/summarize` | 生成总结 → `{ summary, keyPoints, chapters }` |

- 错误响应统一为 `{ error: { code, message } }`：`INVALID_URL`(400) / `UNSUPPORTED_PLATFORM`(422) / `INVALID_TASK`(404) / `TRANSCRIBE_FAILED`(500) 等，完整定义见 `src/lib/types.ts`
- 前端 Mock（Route Handlers）为**无状态**实现：taskId 自含上下文（base64url），进度按时间推算（全程约 10 秒）
- 演示失败路径：`https://www.bilibili.com/video/BV1FailDemo`（60% 处失败，用于验证重试）
- 真实后端模式下：解析由 FastAPI 完成（yt-dlp，仅放行 B站公开链接，服务端拒绝内网/保留地址），
  转写 / 总结暂仍由 Mock 承担，真实解析出的元数据会透传展示

## 后续路线

见 [docs/ROADMAP.md](docs/ROADMAP.md)：M2 多平台支持、M3 本地视频转写、M4 打包桌面 / 移动 App（Tauri / Capacitor）。

## 部署

推荐 [Vercel](https://vercel.com)：

1. GitHub 导入仓库（框架自动识别 Next.js）
2. 无需额外环境变量；后端地址用服务端变量 `BACKEND_ORIGIN`（配合 NEXT_PUBLIC_USE_MOCK=false 与 next.config.mjs 代理）
3. 部署完成后用 Chrome / Edge 打开站点，地址栏出现安装入口（或顶栏「安装应用」按钮），可安装到 Windows 桌面与 Android 主屏幕；iOS 从 Safari「添加到主屏幕」安装

## 协作规范

- 分支：`main`（受保护）← `feat/frontend-demo`（阶段集成分支）← 功能分支；一个功能一个 PR（squash）
- 提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)（`feat:` / `fix:` / `chore:` / `docs:` / `ci:`）
- PR 会自动运行 lint + build 检查（GitHub Actions），绿灯后才能合并
- PR / Issue 模板位于 `.github/`
