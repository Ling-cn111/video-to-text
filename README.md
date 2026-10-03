# video-to-text

视频链接转文字 PWA：粘贴 B站 / 抖音视频链接，自动完成解析、语音转写与 AI 总结，输出带时间戳的文字稿和结构化摘要。可安装到 Windows 桌面与手机主屏幕。

> 当前阶段为**纯前端 Demo**：所有后端能力由 Mock 提供，接口形状按真实后端契约设计（见下文），后续可直接替换。

## 功能

- 粘贴视频链接 → 模拟解析 → 转写（四阶段进度）→ 结果页
- 左侧带时间戳文字稿，右侧 AI 总结（一句话概要 / 要点 / 章节笔记）
- 导出 TXT / Markdown
- PWA：可安装、离线可用（应用外壳）
- 响应式：桌面左右分栏，移动端上下卡片流

## 技术栈

- [Next.js](https://nextjs.org) 14（App Router）+ TypeScript
- Tailwind CSS + [shadcn/ui](https://ui.shadcn.com)
- Zustand 状态管理
- Next Route Handlers 提供 Mock API（`src/app/api`），客户端统一出口 `src/lib/api.ts`
- PWA：Web App Manifest + Service Worker

## 快速开始

```bash
pnpm install
pnpm dev
```

打开 <http://localhost:3000>，使用首页提供的示例链接体验完整流程。

```bash
pnpm lint   # 代码检查
pnpm build  # 生产构建
```

## Mock API 契约

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/parse` | 解析视频链接 → `{ title, cover, duration, platform, videoId }` |
| POST | `/api/transcribe` | 创建转写任务 → `{ taskId, status, progress, stage }` |
| GET | `/api/transcribe/:taskId` | 轮询任务 → `{ status, stage, progress, transcript?, plainText? }` |
| POST | `/api/summarize` | 生成总结 → `{ summary, keyPoints, chapters }` |

错误响应统一为 `{ error: { code, message } }`。完整类型见 `src/lib/types.ts`。

## 后续路线

见 [docs/ROADMAP.md](docs/ROADMAP.md)：多平台支持、本地视频转写、打包桌面 / 移动 App。

## 部署

推荐 [Vercel](https://vercel.com)：导入 GitHub 仓库后零配置部署（框架自动识别 Next.js）。

## 协作规范

- 分支：`main`（受保护）← `feat/frontend-demo` ← 功能分支；一个功能一个 PR（squash）
- 提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)（`feat:` / `fix:` / `chore:` / `docs:` / `ci:`）
- PR 会自动运行 lint + build 检查（GitHub Actions）
