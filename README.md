# video-to-text

视频链接转文字 PWA：粘贴 B站 / 抖音视频链接，自动完成解析、语音转写与 AI 总结，输出带时间戳的文字稿和结构化摘要。可安装到 Windows 桌面与手机主屏幕。

> 当前阶段为**纯前端 Demo**：所有后端能力由 Mock 提供，接口形状按真实后端契约设计（见下文），后续可直接替换。多平台 / 本地视频 / 打包 App 规划见 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 功能

- 粘贴视频链接 → 模拟解析 → 转写（四阶段进度）→ 结果页
- 左侧带时间戳文字稿（点击时间戳可复制），右侧 AI 总结（一句话概要 / 要点 / 章节笔记）
- 导出 TXT / Markdown
- PWA：可安装、离线可用（应用外壳）
- 响应式：桌面左右分栏，移动端上下卡片流
- 错误态：无效链接、不支持平台、转写失败（可重试）

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
src/
├── app/                  # 路由与 API（/、/processing/[taskId]、/result/[taskId]、/api/*）
├── components/           # home / processing / result / shared / layout / pwa / ui(shadcn)
├── lib/                  # types(契约) api(出口) mock-api(无状态Mock) mock-data(假数据)
│                         # platforms/registry(平台注册表) platform(链接解析) url-guard(安全校验)
│                         # export(导出) format(格式化)
├── stores/task-store.ts  # Zustand 任务状态机
└── hooks/                # 轮询等
```

## Mock API 契约

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/parse` | 解析视频链接 → `{ title, cover, duration, platform, videoId }` |
| POST | `/api/transcribe` | 创建转写任务 → `{ taskId, status, progress, stage }` |
| GET | `/api/transcribe/:taskId` | 轮询任务 → `{ status, stage, progress, video?, transcript?, plainText?, error? }` |
| POST | `/api/summarize` | 生成总结 → `{ summary, keyPoints, chapters }` |

- 错误响应统一为 `{ error: { code, message } }`：`INVALID_URL`(400) / `UNSUPPORTED_PLATFORM`(422) / `INVALID_TASK`(404) / `TRANSCRIBE_FAILED`(500) 等，完整定义见 `src/lib/types.ts`
- Mock 为**无状态**实现：taskId 自含上下文（base64url），进度按时间推算（全程约 10 秒），可直接部署到 serverless
- 演示失败路径：`https://www.bilibili.com/video/BV1FailDemo`（60% 处失败，用于验证重试）
- 接入真实后端：设置 `NEXT_PUBLIC_API_BASE_URL` 即可，调用方无需改动

## 后续路线

见 [docs/ROADMAP.md](docs/ROADMAP.md)：M2 多平台支持、M3 本地视频转写、M4 打包桌面 / 移动 App（Tauri / Capacitor）。

## 部署

推荐 [Vercel](https://vercel.com)：

1. GitHub 导入仓库（框架自动识别 Next.js）
2. 无需额外环境变量；如需指向自建后端，设置 `NEXT_PUBLIC_API_BASE_URL`
3. 部署完成后用 Chrome / Edge 打开站点，地址栏出现安装入口（或顶栏「安装应用」按钮），可安装到 Windows 桌面与 Android 主屏幕；iOS 从 Safari「添加到主屏幕」安装

## 协作规范

- 分支：`main`（受保护）← `feat/frontend-demo`（阶段集成分支）← 功能分支；一个功能一个 PR（squash）
- 提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)（`feat:` / `fix:` / `chore:` / `docs:` / `ci:`）
- PR 会自动运行 lint + build 检查（GitHub Actions），绿灯后才能合并
- PR / Issue 模板位于 `.github/`
