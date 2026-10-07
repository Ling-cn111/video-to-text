# 功能路线图（Roadmap）

> **当前状态（2026-10-07）**：核心产品链路已全部交付——
> **阶段一** 真实解析（B站 yt-dlp）✅ → **阶段二** 真实转写（字幕快路径四层降级 + 本地/云端 ASR + 绝对 CER 基准）✅ → **阶段三** 真实 AI 总结（DeepSeek，27 分钟视频 ≈¥0.07）✅。
> 验收报告：[阶段二](acceptance-stage-2.md) / [阶段三](acceptance-stage-3.md)；基准与成本：[asr-benchmark](asr-benchmark.md) / [llm-cost](llm-cost.md)。
> 下文 M2 多平台 / M3 本地视频 / M4 打包 **均未启动**，为后续规划。
> 历史说明：本文成文于 M1 前端 Demo 交付时，M2-M4 编号与项目实际的「阶段一/二/三」编号相互独立；
> 本文记录后续里程碑的设计意向与当前代码中已预留的扩展点。

## M1 前端 Demo（已交付）

- [x] 首页：链接输入 + 解析 + 示例链接（含失败 / 不支持平台演示）
- [x] 转写进度页：视频信息卡 + 四阶段步进 + 整体进度（约 10 秒模拟）
- [x] 结果页：带时间戳文字稿（点击复制）+ AI 总结（概要 / 要点 / 章节笔记）
- [x] 导出 TXT / Markdown
- [x] PWA：可安装、离线外壳（Windows 桌面 / Android 主屏幕）
- [x] 错误态：无效链接、不支持平台、转写失败（可重试）
- [x] 无状态 Mock：taskId 自含上下文，可直接部署到 serverless

## M2 多平台支持

**目标**：在 B站 / 抖音之外支持更多平台。

- 平台清单（按优先级）：YouTube、快手、西瓜视频 / 今日头条、微博、小红书、Twitter / X
- 做法：
  - 在 `src/lib/platforms/registry.ts` 追加条目（host 正则、短链域名、videoId 提取规则、展示配色）——UI 层零改动
  - 扩充 `src/lib/mock-data.ts` 各平台示例数据与首页示例链接
  - 接真实后端时，每个平台对应一个解析器（解析视频元数据与音频流）
- 验收：粘贴对应平台链接即可走通 Mock 全流程

## M3 本地视频转写

**目标**：支持上传本地视频文件转写，与链接转写共用进度 / 结果页。

- 交互：首页新增「上传本地视频」入口（拖拽 + 文件选择），复用 `/processing/:taskId` 与 `/result/:taskId`
- 接口扩展（契约见 `src/lib/types.ts`）：
  - `POST /api/transcribe` 支持 `{ kind: 'file', fileName, size, mimeType }`（真实后端可走 multipart / 分片直传）
  - 任务来源标记 `source: 'url' | 'file'`
  - 新增错误分支：`FILE_TOO_LARGE`、`FILE_FORMAT_UNSUPPORTED`（错误码已在契约中预留）
- 限制：格式 mp4 / mov / webm，大小上限建议 500MB（前端预校验 + 后端强校验）
- 已预留：任务模型以 taskId 为中心、来源无关；错误码已定义

## M4 打包成 App

**目标**：在 PWA 之外提供原生应用分发。

- 桌面端（Windows / macOS）：推荐 **Tauri**——体积小，可加载线上地址或配合 Next 静态导出；系统托盘、全局快捷键可后期加入
- 移动端（Android / iOS）：推荐 **Capacitor** 包壳 PWA，可上架应用商店
- 结构：新增 `apps/desktop`、`apps/mobile` 目录与对应 CI job，与现有 web 应用同仓monorepo（pnpm workspace 已具备条件）
- PWA（M1）与 M4 三端互补：PWA 即装即用，打包 App 覆盖商店分发与系统能力

## 工程预留点速览

| 预留点 | 位置 | 说明 |
| --- | --- | --- |
| 平台注册表 | `src/lib/platforms/registry.ts` | 新平台 = 新条目，UI 自动跟随 |
| 任务来源无关 | `src/stores/task-store.ts` | 进度 / 结果页只依赖 taskId |
| 文件类错误码 | `src/lib/types.ts` | `FILE_TOO_LARGE` / `FILE_FORMAT_UNSUPPORTED` 已定义 |
| API 统一出口 | `src/lib/api.ts` | 换真实后端只改 `NEXT_PUBLIC_API_BASE_URL` |
| URL 安全守卫 | `src/lib/url-guard.ts` | 服务端处理用户 URL 时复用同一份校验 |
