# video-to-text

视频链接转文字 PWA：粘贴 B站视频链接，自动完成解析、语音转写（本地 faster-whisper）与 AI 总结，输出带时间戳的文字稿和结构化摘要。可安装到 Windows 桌面与手机主屏幕。

> **当前进度**：M1 前端 Demo ✅ → **M2 阶段一（真实解析）✅ + 阶段二（真实转写）✅** → 阶段三（真实 AI 总结）、多平台、本地视频、打包 App 规划见 [docs/ROADMAP.md](docs/ROADMAP.md)。阶段二验收报告见 [docs/acceptance-stage-2.md](docs/acceptance-stage-2.md)。

## 功能

- 粘贴 B站视频链接 → 真实解析（yt-dlp）→ 真实转写（字幕快路径优先，否则 faster-whisper 本地 ASR）→ 结果页
- 全文阅读：无时间戳的文章式排版（按章节自动分段，段间空行、行高舒适）
- 带时间戳文字稿（点击时间戳可复制，支持折叠/展开）
- 右侧 AI 总结（一句话概要 / 要点 / 章节笔记，**当前仍为 Mock**）
- 导出 TXT / Markdown（纯文本全文，不含时间戳）
- PWA：可安装、离线可用（应用外壳）
- 响应式：桌面左右分栏，移动端上下卡片流
- 错误态：无效链接、不支持平台、转写失败（可重试）

## 支持的平台

| 平台 | 解析 | 转写 |
| --- | --- | --- |
| 哔哩哔哩（www.bilibili.com / b23.tv） | ✅ yt-dlp | ✅ CC 字幕快路径 / faster-whisper ASR |

> 未登录状态下 yt-dlp 极少能取到 B站字幕（AI 字幕需登录 Cookie），实际以 ASR 路径为主；配置 `BILI_COOKIE` 可启用 AI 字幕快路径（见 backend/README.md）。更多平台在 `src/lib/platforms/registry.ts` 与 `backend/app/platforms.py` 的注册表中追加即可。

## 已知限制

- AI 总结仍为 Mock 数据（阶段三接入真实 LLM）
- 转写任务注册表为进程内存实现：单进程 uvicorn 够用，多 worker / 云端部署需替换为 Redis（接口已封装，见 backend/README.md）
- B站 AI 字幕需登录 Cookie（`BILI_COOKIE`），未配置时自动降级 ASR 路径
- 本地推理依赖 CPU：base 模型转写 2 分钟音频约 30-60 秒；可切换 small 模型提升准确率（更慢）
- FFmpeg 可选：未安装时自动降级 PyAV 解码；建议安装以走标准 16kHz WAV 路径

## 技术栈

- [Next.js](https://nextjs.org) 14（App Router）+ TypeScript
- Tailwind CSS + [shadcn/ui](https://ui.shadcn.com)
- Zustand 状态管理
- Next Route Handlers 提供 Mock API（`src/app/api`），客户端统一出口 `src/lib/api.ts`
- PWA：Web App Manifest + Service Worker（仅生产环境注册）

## 快速开始

```bash
pnpm install
pnpm dev      # 开发模式（不注册 Service Worker）
```

打开 <http://localhost:3000>，使用首页提供的示例链接体验完整流程（含失败 / 不支持平台演示）。

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
