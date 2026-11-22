# 功能路线图（Roadmap）

> **当前状态（2026-11-22 更新）**：核心产品链路已全部交付——
> **阶段一** 真实解析（B站 yt-dlp）✅ → **阶段二** 真实转写（字幕快路径四层降级 + 本地/云端 ASR + 绝对 CER 基准）✅ → **阶段三** 真实 AI 总结（DeepSeek，27 分钟视频 ≈¥0.07）✅ → **M3 本地视频上传** ✅（2026-11-21，OSS 直传 + 500MB，见下文）→ **重点帧抽帧** ✅（2026-11-22，PR19，URL 任务手动触发，见下文）。
> 验收报告：[阶段二](acceptance-stage-2.md) / [阶段三](acceptance-stage-3.md)；基准与成本：[asr-benchmark](asr-benchmark.md) / [llm-cost](llm-cost.md)。
> 下文 M2 多平台**未启动**；M4 打包**已部分交付**（Windows 桌面端与 Android 均发到 v0.3.1，macOS 与 iOS 未启动）。
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

## M3 本地视频转写（已交付 ✅，2026-11-21）

**目标**：支持上传本地视频文件转写，与链接转写共用进度 / 结果页。**已按「客户端直传 OSS + 预签名 PUT」通道交付**。

> 落地形态与本文最初设想（multipart 直传后端）不同：FC HTTP 触发器同步请求体上限 **32MB（平台硬限制）**、
> 异步投递事件体上限 128KB，500MB 级源文件的字节不可能经过任何函数链路——改为「后端签发预签名 PUT →
> 浏览器直传 OSS → 后端只收 objectKey 引用」，连 `python-multipart` 依赖都不需要。部署步骤与费用见
> [serverless-deploy.md](serverless-deploy.md) §4.4。

- 交互 ✅：首页「上传本地视频」入口（拖拽 + 点击），进度条可取消；入口由 `capabilities.fileUploadConfigured` 驱动，未配 OSS 不渲染
- 接口 ✅：`POST /api/upload/init`（签发凭证，只有元数据）+ `POST /api/transcribe { kind: 'file', file: {objectKey, fileName, size} }`；任务来源 `source: 'url' | 'file'`（`TaskSource` 当年的预留兑现）
- 错误码 ✅：`FILE_TOO_LARGE`(413) / `FILE_FORMAT_UNSUPPORTED`(422) / `FILE_STORAGE_NOT_CONFIGURED`(400) / `FILE_NOT_FOUND`(404)，四处同步（前端 `types.ts` / `api-server.ts` / `error-titles.ts` + 后端 `errors.py`）
- 限制 ✅：mp4 / mov / webm，源文件 ≤500MB（前端预校验 + 后端三重校验）；内容超约 50 分钟会在转码阶段报「视频太长」（转码后 WAV 受 `MAX_AUDIO_SIZE_MB=100` 约束 ≈ 52 分钟）
- 关键实现：Worker 从 OSS **内网 endpoint** 流式落 /tmp → ffmpeg 按路径转码（500MB 源文件不进内存）→ 与 URL 任务在 ASR 汇合；真实时长按 WAV 字节数回填；成功删对象 / 失败留 ≤1 天供重试
- E2E：`e2e/pr16-file-upload.spec.ts`（回归 36-36d）；锁定项见 [frontend-features](frontend-features.md) §21

## 重点帧抽帧（已交付 ✅，2026-11-22，PR19）

**目标**：转写完成且 AI 总结就绪后，按总结章节/要点的时间点生成视频画面截图。**已按「URL 任务手动触发二次抽帧」通道交付**。

> 落地前提的实测结论（2026-11-22，`get_video_stream` 的设计依据）：B站未登录 playurl
> 默认 DASH 只下发 480P/360P；请求参数加 `try_look=1`（B站网页播放器未登录态自带的
> 「试看」参数）后直接解锁视频本身的最高档（实测 1080P 全长流，CDN UA+Referer 可下）。
> qn / fnval 位组合 / platform 变体均绕不开。

- 触发 ✅：结果页「生成重点帧」**手动按钮**（抽帧要下载视频流，成本由显式动作承担；不自动触发）；入口由 `capabilities.framesConfigured` + 非 local 平台 + 总结就绪三条件驱动，任一不满足整卡隐藏
- 数据流 ✅：前端从 summary 派生时间点（chapters.timeStart ∪ keyPoints.time，2s 合并、cap 12）→ `POST /api/frames` → 服务端再净化 → FramesJob 投 Worker（fc 模式走 `/internal/worker/frames` 路由，事件体 `kind:"frames"`；inline 本地直跑）→ playurl 取最高 1080P（优先 HEVC）→ ffmpeg 逐点抽帧（1280 宽 JPEG q:v 3，单张 30-50KB）→ OSS `frames/{taskId}/`（生命周期 7 天与任务 TTL 对齐）→ 任务详情 `frames` 字段轮询可见
- 关键实现 ✅：抽帧是 completed 任务上的**二次作业**——不占任务执行锁、不改任务 status，只写 `task.frames`（任务库终态保护加 `TERMINAL_MUTABLE_FIELDS` 例外）；幂等靠 frames 状态机（processing/ready 直接 skip）；单帧失败跳过、整段失败收敛进 `frames.error`（非 HTTP 错误）；预签名 GET 读取时现场签发（限时 1h 不落库，`<img>` 直连无需 CORS）
- 错误码 ✅：`FRAMES_UNSUPPORTED`(422) / `FRAMES_NOT_READY`(409)，四处同步；`FRAMES_FAILED` 只出现在 `frames.error`（异步作业失败收敛）
- E2E：`e2e/pr19-frames.spec.ts`（回归 40-40e）；锁定项见 [frontend-features](frontend-features.md) §24
- **v2 方向**：文件任务重点帧（转码时源在 /tmp 顺手均匀抽帧）；非 B站平台；重点帧导出（图片打包是新二进制分支）

## M4 打包成 App

**目标**：在 PWA 之外提供原生应用分发。

- 桌面端（Windows）：**已交付** ✅ —— Tauri 2 + Next 静态导出，安装包走 GitHub Release（当前 v0.3.1，`.msi` / `.exe`）。**v0.1.5 起带自动更新**：`tauri-plugin-updater` + 签名校验，推 `v*` tag 由 `release-desktop.yml` 自动构建发布。打包流程与踩坑见 [desktop-packaging](desktop-packaging.md)，不变量见 [frontend-features](frontend-features.md) §15
- 桌面端（macOS）：**未启动** —— 同一套 `src-tauri` 配置，但需要 macOS 机器签名与公证；当前仓库无对应 CI job
- 移动端（Android）：**已发布** ✅ —— Capacitor 8 包壳，`pnpm build:mobile` 复用桌面端的静态导出脚本（`scripts/build-shell.mjs` 的 `mobile` 目标），前端资源打进 APK 直连后端，**不依赖域名可达性**。图标、启动图、release 签名配置均已就位，v0.1.4 起在 GitHub Release 分发 APK，v0.1.5 起与桌面端同版本号发布，**v0.1.7 起 APK 由 CI 产出**（`release-desktop.yml` 的 `android` job，不再需要本地手工 `gh release upload`）
  - 构建：`pnpm build:mobile` → `npx cap sync android` → `cd android && ./gradlew --init-script gradle/init-mirrors.gradle assembleDebug`
  - **系统分享接收已打通**：在 B站 App 点「分享 → 视频转文字」直接开转。manifest 只声明 `SEND` + `text/plain`（不声明 `BROWSABLE`，否则每点一次 B站 链接都会弹选择框）；原生插件 `ShareIntentPlugin` 自写（`@capacitor/app` 只处理 `ACTION_VIEW`）；JS 侧 `use-share-intent` 抽链接并把 `b23.tv` 短链还原成 `bilibili.com`（短链会让后端字幕 / playurl 两条快路径失效）
  - **应用内更新已打通（v0.1.8 起）**：设置面板查到新版本后，APK 在 App 内下载并直接拉起系统安装器，不用再跳 GitHub。下载走 `@capacitor/filesystem` 的 `downloadFile`（原生 HTTP；Release 资产 302 到 `objects.githubusercontent.com`，那个域没有 CORS 头，WebView 里 fetch 必然失败）。**仍然做不到全自动静默安装**：Android 只允许 Device Owner / MDM 静默装包，个人应用拿不到，极限就是「下载 + 拉起安装器」，最后那下必须用户点
  - 踩坑与不变量见 [frontend-features](frontend-features.md) §16（`androidScheme` 必须 https、`server.url` 不能配、镜像 init script 不能省、keystore 必须备份）、§17（分享接收五个致命点）、§18（APK 必须由 CI 产出、签名密钥缺失即失败）与 §19（应用内更新的缓存路径要两边一致、缺授权不能循环重试）
- 本地文件上传（M3）：**未启动** —— 需 `@capacitor/camera` / `filesystem` 选本地视频 + 后端 multipart 上传，并新增 `FILE_TOO_LARGE` / `FILE_FORMAT_UNSUPPORTED` 两个错误码（前端 `types.ts` / `api-server.ts` / `error-titles.ts` + 后端 `errors.py` 四处同步，见 `docs/api-contract.md` §3）
- 移动端（iOS）：**未启动** —— 打包必须有 macOS + Xcode，当前开发机是 Windows。同一套前端资源，需补 `npx cap add ios` 与 macOS CI job
- PWA（M1）与 M4 互补：PWA 即装即用（需域名可达），打包 App 覆盖系统分享与离线启动

## 工程预留点速览

| 预留点 | 位置 | 说明 |
| --- | --- | --- |
| 平台注册表 | `src/lib/platforms/registry.ts` | 新平台 = 新条目，UI 自动跟随 |
| 任务来源无关 | `src/stores/task-store.ts` | 进度 / 结果页只依赖 taskId |
| 文件类错误码 | `src/lib/types.ts` | **M3 已引入**：`FILE_TOO_LARGE` / `FILE_FORMAT_UNSUPPORTED` / `FILE_STORAGE_NOT_CONFIGURED` / `FILE_NOT_FOUND`（四处同步完成） |
| API 统一出口 | `src/lib/api.ts` | 换真实后端只改 `BACKEND_ORIGIN`（web 同源代理）或 `NEXT_PUBLIC_API_BASE`（桌面端跨域直连），见 `src/lib/api-base.ts` |
| URL 安全守卫 | `src/lib/url-guard.ts` | 服务端处理用户 URL 时复用同一份校验 |
