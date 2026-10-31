# 功能路线图（Roadmap）

> **当前状态（2026-10-07）**：核心产品链路已全部交付——
> **阶段一** 真实解析（B站 yt-dlp）✅ → **阶段二** 真实转写（字幕快路径四层降级 + 本地/云端 ASR + 绝对 CER 基准）✅ → **阶段三** 真实 AI 总结（DeepSeek，27 分钟视频 ≈¥0.07）✅。
> 验收报告：[阶段二](acceptance-stage-2.md) / [阶段三](acceptance-stage-3.md)；基准与成本：[asr-benchmark](asr-benchmark.md) / [llm-cost](llm-cost.md)。
> 下文 M2 多平台 / M3 本地视频 **均未启动**；M4 打包**已部分交付**（Windows 桌面端与 Android 均发到 v0.1.5，macOS 与 iOS 未启动）。
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

**目标**：在 B站之外支持更多平台（抖音目前只存在于前端 Mock 的演示数据里，真实后端未接入）。

- 平台清单（按优先级）：抖音、YouTube、快手、西瓜视频 / 今日头条、微博、小红书、Twitter / X
- 做法：
  - 在 `src/lib/platforms/registry.ts` 追加条目（host 正则、短链域名、videoId 提取规则、展示配色）——UI 层零改动
  - **同时**在后端 `backend/app/platforms.py` 的 `PLATFORM_REGISTRY` 注册同名平台，并把平台加进 `SUPPORTED_PLATFORMS`
  - 扩充 `src/lib/mock-data.ts` 各平台示例数据与首页示例链接
  - 接真实后端时，每个平台对应一个解析器（解析视频元数据与音频流）
- **顺序不能反**（锁定项 §12）：先接后端、再把平台加进 `SUPPORTED_PLATFORMS`。反了就是对外虚假宣称——前端放行、后端 422
- 验收：粘贴对应平台链接即可走通 Mock 全流程；真实构建下 `SUPPORTED_PLATFORMS` 与实际解析能力一致

## M3 本地视频转写

**目标**：支持上传本地视频文件转写，与链接转写共用进度 / 结果页。

- 交互：首页新增「上传本地视频」入口（拖拽 + 文件选择），复用 `/processing/:taskId` 与 `/result/:taskId`
- 接口扩展（契约见 `src/lib/types.ts`）：
  - `POST /api/transcribe` 支持 `{ kind: 'file', fileName, size, mimeType }`（真实后端可走 multipart / 分片直传）
  - 任务来源标记 `source: 'url' | 'file'`
  - 新增错误分支：`FILE_TOO_LARGE`、`FILE_FORMAT_UNSUPPORTED`（M3 落地时一并引入：前端 `types.ts` / `api-server.ts` / `error-titles.ts` + 后端 `errors.py`，四处同步，见 `docs/api-contract.md` §3）
- 限制：格式 mp4 / mov / webm，大小上限建议 500MB（前端预校验 + 后端强校验）
- 已预留：任务模型以 taskId 为中心、来源无关（错误码不再预留——P2 已清理无生产者的 `FILE_TOO_LARGE` / `FILE_FORMAT_UNSUPPORTED`，做 M3 时再加，避免契约里挂着没人产出的码）

## M4 打包成 App

**目标**：在 PWA 之外提供原生应用分发。

- 桌面端（Windows）：**已交付** ✅ —— Tauri 2 + Next 静态导出，安装包走 GitHub Release（当前 v0.1.7，`.msi` / `.exe`）。**v0.1.5 起带自动更新**：`tauri-plugin-updater` + 签名校验，推 `v*` tag 由 `release-desktop.yml` 自动构建发布。打包流程与踩坑见 [desktop-packaging](desktop-packaging.md)，不变量见 [frontend-features](frontend-features.md) §15
- 桌面端（macOS）：**未启动** —— 同一套 `src-tauri` 配置，但需要 macOS 机器签名与公证；当前仓库无对应 CI job
- 移动端（Android）：**已发布** ✅ —— Capacitor 8 包壳，`pnpm build:mobile` 复用桌面端的静态导出脚本（`scripts/build-shell.mjs` 的 `mobile` 目标），前端资源打进 APK 直连后端，**不依赖域名可达性**。图标、启动图、release 签名配置均已就位，v0.1.4 起在 GitHub Release 分发 APK，v0.1.5 起与桌面端同版本号发布，**v0.1.7 起 APK 由 CI 产出**（`release-desktop.yml` 的 `android` job，不再需要本地手工 `gh release upload`）
  - 构建：`pnpm build:mobile` → `npx cap sync android` → `cd android && ./gradlew --init-script gradle/init-mirrors.gradle assembleDebug`
  - **系统分享接收已打通**：在 B站 App 点「分享 → 视频转文字」直接开转。manifest 只声明 `SEND` + `text/plain`（不声明 `BROWSABLE`，否则每点一次 B站 链接都会弹选择框）；原生插件 `ShareIntentPlugin` 自写（`@capacitor/app` 只处理 `ACTION_VIEW`）；JS 侧 `use-share-intent` 抽链接并把 `b23.tv` 短链还原成 `bilibili.com`（短链会让后端字幕 / playurl 两条快路径失效）
  - 踩坑与不变量见 [frontend-features](frontend-features.md) §16（`androidScheme` 必须 https、`server.url` 不能配、镜像 init script 不能省、keystore 必须备份）、§17（分享接收五个致命点）与 §18（APK 必须由 CI 产出、签名密钥缺失即失败）
- 本地文件上传（M3）：**未启动** —— 需 `@capacitor/camera` / `filesystem` 选本地视频 + 后端 multipart 上传，并新增 `FILE_TOO_LARGE` / `FILE_FORMAT_UNSUPPORTED` 两个错误码（前端 `types.ts` / `api-server.ts` / `error-titles.ts` + 后端 `errors.py` 四处同步，见 `docs/api-contract.md` §3）
- 移动端（iOS）：**未启动** —— 打包必须有 macOS + Xcode，当前开发机是 Windows。同一套前端资源，需补 `npx cap add ios` 与 macOS CI job
- PWA（M1）与 M4 互补：PWA 即装即用（需域名可达），打包 App 覆盖系统分享与离线启动

## 工程预留点速览

| 预留点 | 位置 | 说明 |
| --- | --- | --- |
| 平台注册表 | `src/lib/platforms/registry.ts` | 新平台 = 新条目，UI 自动跟随 |
| 任务来源无关 | `src/stores/task-store.ts` | 进度 / 结果页只依赖 taskId |
| 文件类错误码 | `src/lib/types.ts` | M3 需要时新增 `FILE_TOO_LARGE` / `FILE_FORMAT_UNSUPPORTED`（P2 已清理占位码，同步四处） |
| API 统一出口 | `src/lib/api.ts` | 换真实后端只改 `BACKEND_ORIGIN`（web 同源代理）或 `NEXT_PUBLIC_API_BASE`（桌面端跨域直连），见 `src/lib/api-base.ts` |
| URL 安全守卫 | `src/lib/url-guard.ts` | 服务端处理用户 URL 时复用同一份校验 |
