# ⚠️ 前端功能锁定协议（Frontend Feature Lock）

> **【强制警告】后续任何 AI 或开发者修改前端代码之前，必须先通读本文档。
> 下列功能均为用户明确确认过的交付物，严禁在重构、新功能开发或修复中覆盖、删除或弱化。
> 每次改动前端后，必须对照文末「回归检查清单」逐项验证，并在 PR 描述中附验证结果。**
>
> **本协议已由 `e2e/locked-features.spec.ts` 自动化强制（Playwright，Mock 模式），CI 中 PR 必须 E2E 绿灯才能合并。**
>
> 背景教训：PR#11（全文阅读 + 无时间戳导出）曾因合入集成分支而未进入 main，导致功能回退且未被发现（见 docs/acceptance-stage-2.md）。本协议即为防止再次发生。

## 1. 结果页（/result/[taskId]）

| 功能 | 锁定要求 |
| --- | --- |
| **双视图 Tab 切换** | 文字稿区提供两个 Tab：**`全文阅读`（默认激活）** 与 **`时间戳定位`**。不得移除 Tab 结构、不得改默认 Tab |
| 全文阅读视图 | 无时间戳、无 `[00:00]` 标记的文章式排版：按语义分段（章节对齐 + 句长兜底 + 超长段落切分）、段落空行、行高 1.9、两端对齐、内容限宽；带字数与预计阅读时长徽章 |
| 全文阅读辅助工具（P1-9） | 正文上方工具条：三档字号 `data-testid="article-font-size"`（`localStorage` 键 `readingFontSize`，刷新保留）、段落搜索 `data-testid="article-search"`、章节目录开关 `data-testid="toggle-toc"`。目录 `data-testid="article-toc"` 仅在总结含 chapters 时渲染。搜索高亮用 `<mark>` 只改样式、**不得改变正文 `textContent`**（否则复制/导出与页面不一致）。**`data-testid="fulltext-article"` 只包正文，目录与工具条必须在它外面** |
| 时间戳定位视图 | 逐句列表，句首时间戳可点击复制（`[mm:ss] 文本`），带复制成功反馈；Badge「点击时间可复制」保留。列表高度自适应（`max-h-` 必须加在 ScrollArea 的 **viewport** 上，不能加在 Root 上——Root 是 `overflow-hidden` 裁剪层，加错会让长稿「看着能滚但滚不动」） |
| 复制全文按钮 | 结果页头部动作区与「导出」并列新增 `data-testid="copy-fulltext-button"`：点击复制「全文阅读」正文纯文本（`formatTranscriptToArticle` 派生段落，`paragraphs.join('\n\n')`，无时间戳），与页面全文视图逐段一致；桌面 / 移动端均不遮挡标题与元信息行 |
| 复制反馈与降级 | 优先 `navigator.clipboard.writeText`，失败降级临时 `textarea` + `document.execCommand('copy')`；成功 toast「已复制全文」+ 按钮 1.5 秒内图标切 Check、文案变「已复制」，失败 toast「复制失败，请手动选择正文复制」，**严禁静默失败**；无可复制内容时按钮禁用 |
| AI 总结面板 | 右侧独立卡片：一句话概要、**3-5 条要点（每条带原文字稿时间戳徽章，`data-testid="key-points"`）**、章节笔记（**时间区间 `mm:ss–mm:ss`，`data-testid="chapter-timeline"`**，字段 `{title, timeStart, timeEnd, note}`）。**修改总结 UI 时严禁波及左侧 Tab 结构**。阶段三起由真实 LLM 生成（E2E 锁定 6；失败降级见下条） |
| 总结失败降级（任务 M2-P0） | 总结失败**不得拖垮整页**：文字稿/全文阅读/导出照常可用，仅总结区显示错误卡（`data-testid="summary-error"`）+「重试总结」按钮；加载中为骨架（`summary-skeleton`）；未总结态为引导文案 +「生成 AI 总结」按钮（`summary-manual-prompt` / `manual-summarize-button`，任务 M2-P3 由原空态 `summary-empty` 升级而来，手动生成与错误重试共用 `retrySummary` 逻辑） |
| 刷新恢复 | 直接刷新 / 直链访问按 taskId 从接口恢复，不白屏 |

## 2. 导出（无时间戳铁律）

| 功能 | 锁定要求 |
| --- | --- |
| TXT 导出 | `《标题》` + `来源：xx ｜ 时长：x 分 x 秒`（中文时长，**严禁 12:34 形式的类时间戳写法**）+ 纯文本段落。**全文件零时间戳** |
| Markdown 导出 | `# 标题` + `> 元信息` + **`## 正文`** + 段落（空行分隔）。**全文件零时间戳**，可直接贴入 Notion / Obsidian |
| 导出入口 | 结果页头部「导出」下拉菜单，分两组：**「导出全文」**（TXT / MD，带「纯文本 · 不含时间戳」说明）与**「导出字幕」**（SRT / VTT，带「带时间轴 · 可直接挂到播放器」说明）。两组不得合并为一组 |
| 字幕导出（SRT / VTT） | 由 `lib/subtitle-export.ts` 的 `buildSrt` / `buildVtt` 生成：SRT 时间码 `HH:MM:SS,mmm`（逗号），VTT 时间码 `HH:MM:SS.mmm`（点号）且首行必须是 `WEBVTT`。每条字幕的结束时间取下一条的起点，最后一条用视频时长兜底；超长句按标点拆分为多条，拆分后拼回必须与原文逐字一致。文件名 `<标题>-字幕.<格式>` |
| 两类导出语义相反，严禁混用 | TXT / MD 是**阅读产物**，全文件零时间戳（§2 铁律）；SRT / VTT 是**字幕产物**，时间轴就是其全部意义。**不得往 `buildPlainText` / `buildMarkdown` 里塞时间戳，也不得让字幕导出复用文章分段逻辑**。改动任一侧都必须确认另一侧未被污染 |
| 分段来源 | 导出段落 = 页面全文视图段落（`formatTranscriptToArticle`），两者必须保持一致 |
| 复制全文同源 | 复制全文按钮的剪贴板内容 = `formatTranscriptToArticle` 派生段落（`paragraphs.join('\n\n')`），与页面全文视图逐段一致、全文件零时间戳（E2E 锁定 11） |

## 3. 转写进度页（/processing/[taskId]）

| 功能 | 锁定要求 |
| --- | --- |
| 真实进度 | 进度条与百分比来自后端任务接口的真实进度（阶段锚点：parse_link 5% → extract_audio 15-35% → asr 40-95%），**不得改回 Mock 的固定 10 秒推算**（Mock 模式除外） |
| 四阶段步进 | 解析链接 → 提取音频 → 语音识别 → 生成总结，完成打勾 / 进行中脉冲 |
| 失败重试 | 任务失败显示友好中文错误 + 重试按钮（重建任务）+ 返回首页 |
| 任务中断 | 服务重启后未完成任务（后端 `status=interrupted`）显示「任务已中断（服务重启过），请重试」+ 重试按钮（复用失败错误卡路径，任务 H） |
| 演示文案 | 「演示模式：转写全程约 10 秒」提示**仅 Mock 模式显示**（真实模式严禁出现） |
| 剩余时间预估（P1-8） | `data-testid="remaining-time"` 显示「本阶段预计还需约 N 秒 / N 分钟」。**只估当前阶段，禁止用总进度线性外推**：进度是分段锚定的（parse 只占 5%），线性外推会在开头 5% 得出「还剩 8 秒」然后卡两分钟。采样窗口不足 3 秒或进度未推进时**不显示**（宁可不显示也不显示错数） |
| 轮询节奏（P1-8） | 阶段开始 10 秒内 800ms 一次，之后降到 3000ms；页面不可见（`visibilitychange`）时**暂停轮询**，切回前台立即补一次。**不得改回全程固定 800ms**：真实 ASR 40-155 秒会打近 200 次 GET，浪费 Serverless 调用与移动端电量 |
| 离开出口（P1-8） | `data-testid="stop-waiting"` 按钮文案「不等了，先回首页」+ 提示「转写会继续跑，回首页「最近转写」还能找回来」。**不得改叫「取消任务」**：后端无 DELETE 路由、pipeline 无取消检查点、任务无 cancelled 状态，标 failed 不会让正在跑的转写停下，那是欺骗用户 |
| 离开保护 | 转写/总结进行中注册 `beforeunload`。**只拦浏览器级离开**（关闭标签页 / 刷新），应用内跳转由「不等了」按钮负责——再弹确认框是二次打扰 |

## 4. Mock / 真实切换

| 功能 | 锁定要求 |
| --- | --- |
| 开关 | `NEXT_PUBLIC_USE_MOCK=false` 时解析与转写走真实后端（`/backend-api/*` 同源代理 → FastAPI）；其余情况走内置 Mock Route Handlers |
| 代理 | 真实请求一律走同源 `/backend-api/*`（next.config.mjs rewrites → `BACKEND_ORIGIN`），浏览器不直连后端 |
| 客户端实现 | `lib/real-api.ts` 的 parse / transcribe(POST) / getTask(GET) / **summarize(POST /backend-api/summarize，阶段三已切换：请求 `{transcript,title,duration}` 无状态)** 均指向 `/backend-api/*`；切换已完成并同步本文件（任务 E） |
| Mock 降级 | Mock 总结对真实 taskId（uuid）降级返回演示总结；Mock 模式的示例链接（含失败 / 不支持平台演示）保留 |

## 5. 其他已确认功能

| 功能 | 锁定要求 |
| --- | --- |
| 错误提示 | 后端错误形状 `{ error: { code, message } }` 统一转友好中文 toast / 错误卡；SSRF 守卫（url-guard）不得删除 |
| ASR 文本后处理 | 繁→简统一（zhconv）、重复词折叠（3 次以上）、按语言补句末标点——backend 侧实现，前端文案展示依赖其输出 |
| PWA | manifest + Service Worker（仅生产注册），顶栏安装按钮。**改动 SW 策略或发布影响外壳的变更时必须提升 `CACHE_NAME` 版本号**（`vtt-shell-vN`）：`activate` 只清理非当前版本缓存，不升版本会让老客户端长期运行旧版 JS（2026-10 线上旧文案问题即此因） |
| 全文导出一致性 | 页面全文视图、TXT、MD 三者的段落必须同源（`formatTranscriptToArticle`），改分段逻辑必须三处一起验证 |
| 错误码表四处同步 | 新增/删除错误码必须同步：`src/lib/types.ts` 的 `ApiErrorCode`、`src/lib/api-server.ts` 的 `STATUS_BY_CODE`（穷举，漏改 TS 报错）、`src/lib/error-titles.ts` 的中文标题表、`docs/api-contract.md`。**`RATE_LIMITED`（429）必须显示「请求太频繁」并进入冷却**，冷却期内禁用重试按钮——重试本身也是 POST，冷却期点必然再 429 |
| 限流冷却时长取 `Retry-After` | 冷却秒数必须优先用 429 响应的 `Retry-After`（`src/lib/http.ts` 解析 → `ApiError.retryAfter` → `UiError.retryAfter` → `useRateLimitCooldown(rateLimited, retryAfter)`），**不得写死 60 秒**：后端是令牌桶按 `per_minute/60` 回填，默认 10 次/分钟时 6 秒就够一个令牌，写死会让用户白等 54 秒。仅在响应缺少该头时用 `RATE_LIMIT_COOLDOWN_SECONDS=60` 兜底 |
| 未配置提示分环境 | 云端 ASR / LLM 未配置的提示必须按 hostname 分流（`src/lib/capability-hint.ts`）：本地给配置指引（可提环境变量名），公网只说「暂不可用」。**线上访客没有 `.env` 也没有 setup.bat，暴露这些只会造成困惑**（同 §9 的 run.bat 教训）。两条分支的出路**不同，不能互相抄**：AI 总结必须指向「右上角 API 设置」（BYOK，见 §14）；云端转写走站点的免费模型、界面不收集 ASR Key，公网分支只能建议改用本地模式，**不得让用户去填 Key** |
| 偏好持久化 | `autoSummary` / `engine` / `summaryDetail` / `readingFontSize` 均存 `localStorage`，键名不得更改；`engine` 读到非法值必须回落 `local`（§6 默认本地）。新增加载期读取的偏好一律走 `hydratePreferences` 或挂载后 `useEffect`，**不要直接读 `localStorage` 初始化 state**（SSR 会 hydration mismatch） |

## 6. 转写模式选择（首页，任务 D 新增；P0-2 改为按部署显示）

| 功能 | 锁定要求 |
| --- | --- |
| **只在能跑本地 ASR 的部署显示** | 选择器 `data-testid="transcribe-mode"` 的渲染条件是 `capabilities.localAsrAvailable === true`（后端探测 `faster_whisper` + `av` 是否可导入）。**线上（Vercel + 阿里云 FC）镜像按 `requirements-cloud.txt` 构建，不含这两个包，因此网页端与桌面端都不显示选择器**。理由：给一个必然失败的按钮，还要用户先理解「本地 / 云端」这两个词，是纯粹的负担。Mock 模式下 `/api/capabilities` 返回 `localAsrAvailable: true`，演示站仍展示完整选择器 |
| **请求参数随能力走** | `submitUrl` / `retry` 调 `POST /api/transcribe` 时，只有 `localAsrAvailable === true` 才带 `engine`；否则传 `undefined`，由后端 `_resolve_engine` 按「有云端 Key 走云端 → 否则本地可用走本地 → 都没有报错」自动挑。**绝不能一直带 `engine: 'local'`**：线上没有本地引擎，会 `ModuleNotFoundError` 变成「转写失败」，而正确行为是走云端 |
| 默认本地 | 选择器可见时默认选中**本地**（faster-whisper small），不得更改默认值 |
| 模式说明常显（M2-P2-B） | 选择器下方常显说明行 `data-testid="mode-description"`，文案随选择变化：本地「本地模式：免费 · 离线 · 较慢，准确率中等」/ 云端「云端模式：更快 · 准确率更高 · 音频将上传至第三方服务」（云端态 amber + 盾牌图标，**隐私提示已并入本行**）。该说明行不得移除、不得改为仅悬停提示 |
| 云端能力态（M2-P2-A） | `cloudAsrConfigured=false` 时云端选项禁用并显示配置引导；本地选项始终可用 |
| Mock 模式小字 | Mock 模式下选择器下方显示「当前为 Mock 演示模式，此选择无实际效果」小字；**真实模式严禁出现该提示**（由 `NEXT_PUBLIC_USE_MOCK` 控制渲染） |
| 纯手动 | 选择器可见时引擎选择纯手动，禁止加入任何基于 VAD / 信噪比 / 时长的自动切换逻辑。**「后端按能力自动挑」与这条不冲突**：前者只在用户没有选择权（选择器不渲染）时生效，不是替用户做他看得见的选择 |

## 7. 文字稿来源标识（任务 G 新增）

| 功能 | 锁定要求 |
| --- | --- |
| 字幕快路径优先 | 有字幕的视频优先走字幕快路径（降级链：yt-dlp CC → dm/view AI → 预留 Cookie 层 → ASR），后端标记 `transcriptSource`（subtitle_cc / subtitle_ai / asr），降级链不得移除 |
| 字幕分句（P0-1） | 字幕命中后必须经 `backend/app/services/subtitles.py` 的 `punctuate_subtitle_items` 合并分句并补句末标点。B站 AI 字幕按口播节奏切成 11 字一条、零标点（实测 171/171 条无句末标点），不合并会让 `src/lib/article.ts` 的 `SENTENCE_END` 永不命中，全文阅读退化成 500 字一堵的硬切长墙。**参数 `gap=1.2s / min=24 / max=60` 实测把 171 条压到 58 句、前端分段从 4 段恢复到 12 段**，改动需重跑 `test_punctuate_*` |
| 分句不得引入时间戳 | 分句只改 `text` 内容，**绝不允许把时间戳写进文本**（§2 全文导出零时间戳铁律，回归 `test_punctuate_does_not_introduce_timestamps`）。句首时间取该句第一条的时间，保证时间戳定位仍准确 |
| 来源 Badge | 结果页 `data-testid="source-badge"` 按 `transcriptSource` 渲染：**字幕来源（subtitle_cc / subtitle_ai）显示「来源：B站字幕」；ASR 来源显示「来源：语音识别」；缺省不显示**。三个分支均不得移除（用户需据此区分字幕快路径与 ASR，任务 M2-P1 变更） |
| 增量原则 | Badge 为头部元信息行增量，不得借机改动 Tab 结构 / 导出 / 进度页等已锁定功能 |

## 8. 首页模式标识（任务 K 新增）

| 功能 | 锁定要求 |
| --- | --- |
| Mock 徽章 | 首页 Hero 徽章 `data-testid="mock-mode-badge"` 文案「演示模式 · Mock 数据」，**仅在 Mock 模式渲染**；真实模式 **严禁出现**任何 Mock 标识 |
| Mock 说明段 | 首页底部演示说明段 `data-testid="mock-mode-note"` 仅 Mock 模式渲染；真实模式隐藏 |
| 动态渲染铁律 | 模式相关文案必须由构建期常量 `NEXT_PUBLIC_USE_MOCK` 驱动，**禁止写死**（历史教训：旧版「前端 Demo · Mock 数据演示」不随模式变，真实模式下误导使用者） |

## 9. 后端可达性横幅（全局，任务 M2-P3 新增）

| 功能 | 锁定要求 |
| --- | --- |
| 探测节奏 | 应用加载即探测一次，之后每 15 秒一次；单次 8 秒超时。失败 / 超时 / 非 2xx 一律判定不可达 |
| 横幅 | `data-testid="backend-unreachable-banner"`，`role="alert"`，文案含「后端服务不可达」；固定顶部（fixed + 等高占位） |
| 文案分环境 | **本地**（`localhost` / `127.0.0.1` / `::1`）追加「请确认已启动本地服务（运行 run.bat start）」；**公网**只提示「请稍后重试」。原因：线上访客没有本地脚本，写 run.bat 只会造成误解（2026-10 线上误报教训） |
| 超时为何是 8 秒 | 真实模式请求要经 Vercel 边缘节点代理到后端，跨境链路首屏并发加载常超 3 秒。原 3 秒会把「慢」误判成「挂了」，闪出假故障横幅 |
| 关闭与恢复 | 可手动关闭（关闭按钮 `aria-label="关闭提示"`）；后端恢复后自动隐藏；恢复后再次故障重新提示 |
| 端点 | 真实模式 `/backend-api/health`（后端 `GET /api/health`）；Mock 模式同源 `/api/health`（恒 200，E2E 拦截制造失败）。探测响应**不得包含任何配置 / Key 信息** |
| 布局 | 不可见时不渲染任何节点；可见时留出顶部空间（占位 + 顶栏 sticky 偏移 `--backend-banner-offset`），不得遮挡或挤压既有页面内容 |

## 10. 解析后 AI 总结开关（首页，任务 M2-P3 新增）

| 功能 | 锁定要求 |
| --- | --- |
| 默认开启 | 首页开关 `data-testid="auto-summary-toggle"`（`role="switch"`，`aria-checked`）默认开启；位于解析表单与转写模式选择器之间 |
| 关闭行为 | 关闭后转写照常完成（后端契约不变），前端**不发起** summarize 请求；结果页显示未总结引导 +「生成 AI 总结」按钮 |
| 持久化 | 状态存 localStorage 键 `autoSummary`（仅布尔值，**严禁存任何 Key 或隐私信息**）；刷新、重开页面保持 |
| 两条路径一致 | 轮询完成（`pollTask`）与刷新 / 直链恢复（`recover`）都必须尊重开关 |
| 未配置 LLM Key | 没有可用 Key（站点未配 **且** 用户未填自己的，见 §14）时：**自动总结路径不发起请求、也不报红卡**，直接落到未总结引导态（`summary-manual-prompt`）；引导态的主操作换成「填写自己的 Key」（`data-testid="summary-open-api-settings"`，点击派发 `api-settings:open` 打开设置面板），不渲染灰掉的「生成 AI 总结」——点一万次也不会成功。填完 Key 后靠 `byok:changed` 立刻恢复成可生成，不用刷新 |
| 纯前端控制 | 开关不进入任何接口请求体，`/api/transcribe`、`/api/summarize` 契约不变 |
| 总结详细度（P1-11） | 结果页总结卡头部三档切换 `data-testid="summary-detail-switch"`（精简 / 标准 / 详细），每档 `data-testid="summary-detail-<brief\|standard\|detailed>"` + `aria-pressed`；选择存 `localStorage` 键 `summaryDetail`，刷新保留；缺省 `standard`。**切换必须重新请求 LLM，禁止本地裁剪 keyPoints**——精简档要的是更短的句子，本地砍条数只会得到「详细版被砍一半」 |
| 总结缓存键带档位 | 总结缓存的 key 必须是 `summary:<taskId>:<detail>`。**只按 taskId 缓存会让用户切到「详细」却拿回「精简」的旧结果，缓存反而成 bug** |

## 11. 最近转写记录（首页，P1-7 新增）

| 功能 | 锁定要求 |
| --- | --- |
| 位置与内容 | 首页 `data-testid="recent-tasks"`，位于输入区与「工作流程」之间；每条 `data-testid="recent-task-item"` 显示标题、相对时间（刚刚 / N 分钟前 / N 小时前 / N 天前）、状态入口 |
| 存储 | `localStorage` 键 `recentTasks`，最多 8 条，**只存 taskId / 标题 / 封面 / 时长 / 平台 / 时间 / 状态，严禁存任何 Key、文字稿或总结内容**；脏数据必须过滤掉而不是整块崩掉 |
| 状态分流 | `status='processing'` → 「查看进度」跳 `processingHref`；`status='done'` → 「查看结果」跳 `resultHref`。失败 / 中断的任务必须从列表移除，不留死链 |
| 时间戳不得被刷新的场景污染 | 结果页每次挂载都会走 `recover`，**必须用「仅在不存在时补记」的语义**（`ensureRecentTask`），无条件 `record` 会把 `savedAt` 刷成「刚刚」，相对时间就失去意义 |
| 空态 | 无记录时不渲染任何节点（不要留空卡片占位） |

## 12. 平台宣称与能力一致（P0-4 新增）

| 功能 | 锁定要求 |
| --- | --- |
| 宣称范围随能力 | 对外宣称支持哪些平台，必须由 `src/lib/platforms/registry.ts` 的 `SUPPORTED_PLATFORMS` 单一来源生成，**禁止在文案里写死平台名**。首页 Hero、特性卡、输入框 placeholder、`metadata.description`、manifest 的 description 全部走 `supportedPlatformsText()` / `supportedPlatformsShortText()` |
| 与后端能力对齐 | `SUPPORTED_PLATFORMS` 必须等于「本构建真能转写的平台」。真实后端只接了 bilibili（`backend/app/platforms.py` 的 `PLATFORM_REGISTRY`），故真实模式只宣称 B站；Mock 模式含 douyin。**新增平台时先接后端，再改 `SUPPORTED_PLATFORMS`**，顺序反了就是虚假宣称 |
| 三种失败原因分开 | `resolveVideoUrl` 的失败必须区分：`invalid`（不是视频链接）/ `unsupported`（不认识这个站，如 v.qq.com）/ `unavailable`（认得域名但本构建没接，如真实模式下的抖音）。**`unavailable` 的文案不得说「暂不支持」**，那是「永远不支持」的语感；应说「暂未支持」，并给出当前可转写的范围 |
| 示例链接同步 | 首页示例链接只列 `SUPPORTED_PLATFORMS` 内的平台；「不支持平台演示」「转写失败演示」两条固定演示项始终保留（属 Mock 教学用途） |
| 禁止虚假功能承诺 | 特性卡与 Hero 不得承诺未实现的行为（历史教训：「点击时间戳即可跳转对应画面」曾与「只复制不跳转」的实际行为不符） |

## 13. 错误边界与站点元信息（P1-2 新增）

| 功能 | 锁定要求 |
| --- | --- |
| 404 页 | `src/app/not-found.tsx`，`data-testid="not-found"`，中文文案 + 返回首页出口。**不得回退到 Next 默认的英文 "This page could not be found"** |
| 路由错误边界 | `src/app/error.tsx`，`data-testid="route-error"`，必须同时给「重试」（调 `reset()`）与「返回首页」两条出路，并显示 `error.digest`——生产环境 Next 只保留 digest，用户截图报障时这是唯一线索 |
| 根级错误边界 | `src/app/global-error.tsx`，**必须自带 `<html>` / `<body>` 且只用内联样式**：根布局已渲染失败时不能依赖 `layout.tsx` 与 Tailwind 类，样式表也可能没加载。文案与 `error.tsx` 必须不同（E2E 回归 10b 靠文案区分两者是否都在产物里） |
| robots.txt | `src/app/robots.ts` 生成。**必须 Disallow `/processing` 与 `/result`**：任务页靠 `?id=` 定位，抓下来是空壳，且任务 7 天后过期（`TASK_TTL_SECONDS=604800`），收录了就是死链。必须给出 `Sitemap:` |
| sitemap.xml | `src/app/sitemap.ts` 生成，**只列首页**。任务页同上理由不得收录 |
| 站点绝对地址 | `src/lib/site.ts` 的 `SITE_URL` 是唯一来源，`metadataBase` / robots / sitemap 共用。换域名只改这里（或用 `NEXT_PUBLIC_SITE_URL` 覆盖），**不要在多个文件里各写一遍域名** |
| 安全响应头 | `next.config.mjs` 的 `headers()`，**只加在 web 分支**（静态导出由 Tauri asset 协议加载，没有 HTTP 头可加）。必须含 CSP（`default-src 'self'` / `object-src 'none'` / `frame-ancestors 'none'` / `base-uri 'self'`）、`X-Frame-Options: DENY`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: strict-origin-when-cross-origin`（任务页 URL 含 taskId，不能整条泄露给第三方） |
| CSP 的 `'unsafe-inline'` | `script-src` 必须保留 `'unsafe-inline'`：next-themes 在 `<head>` 内联一段脚本，用于首帧前把 `.dark` 写进 `<html>`。**不要为了「更严格」去掉它**，那会让深色用户每次刷新看到白闪 |
| 缓存策略 | `/_next/static/*` 长缓存 `immutable`（文件名带内容哈希）；`/sw.js` 必须 `no-cache, no-store`，否则老客户端长期跑旧外壳（与 §5 的 `CACHE_NAME` 提升是两道独立的防线，都要） |
| 深色模式 | 根布局挂 `ThemeProvider`（`attribute="class"`、`defaultTheme="system"`、`enableSystem`、`disableTransitionOnChange`）。`globals.css` 的 `.dark` 变量早于开关存在，**开关缺失时那套变量等于死代码**。顶栏 `data-testid="theme-toggle"`，三档 `theme-light` / `theme-dark` / `theme-system`，选择存 localStorage 的 `theme` 键（next-themes 自管，不进 `hydratePreferences`） |
| 主题图标防闪烁 | `ThemeToggle` 必须 `mounted` 之后才画确定图标：服务端不知道用户主题，首帧就画「太阳」会让深色用户看到闪一下 |
| 深色可读性 | 深色态下 `body` 前景亮度必须高于背景（E2E 回归 13b 用亮度公式断言）。新增带底色的高亮（如搜索结果）必须成对给 `dark:` 变体，否则深色下会是刺眼的浅黄块 |
| PWA 缓存版本 | 改动根布局（如本次挂 `ThemeProvider`）属于「影响外壳的变更」，**必须提升 `public/sw.js` 的 `CACHE_NAME`**（v3 → v4），见 §5 |

## 14. 用户自带 Key（BYOK，新增）

| 功能 | 锁定要求 |
| --- | --- |
| 入口位置 | 顶栏 `data-testid="open-api-settings"`，与主题切换、PWA 安装并列。**必须是全局设置而非首页表单里的选项**：它对结果页的总结生效，跟着页面走会让人以为只对当前这次转写有用。**移动端壳里这个按钮必须带可见文字**（`IS_MOBILE_SHELL` 为真时标签不再用 `hidden sm:inline`，`span` 真的可见，另带 `aria-label`）：手机顶栏只有三四个图标的位置，一个没有文字的钥匙图标混在主题切换旁边等于不存在，真机反馈原话是「手机端没看到有设置选项」（`verify-shell.mjs mobile` 断言文字可见） |
| 面板 | `data-testid="api-settings-dialog"`，收「AI 总结服务」一组：**Key + 接口地址 + 模型名**三项，testid 分别为 `byok-llm-input` / `byok-llm-base-url-input` / `byok-llm-model-input`，配 `byok-llm-save` / `byok-llm-clear` / `byok-llm-masked` / `byok-llm-error` |
| **三项必须都能配** | 只让用户填 Key 是不够的：Key 属于某个服务商，请求要打到**那个服务商**的地址、用**那个服务商**的模型名。旧实现把地址与模型钉死在后端 `LLM_PRESETS`，用户填 OpenAI / Moonshot 的 Key，后端仍拿它去请求 DeepSeek → 必然 401。这就是「别家的 Key 用不了」的根因，**不得回退成只收 Key** |
| 服务商预设 | `byok-preset-<id>` 一排按钮（`LLM_PROVIDER_PRESETS`：DeepSeek / 百炼 / Moonshot / 智谱 / 硅基流动 / OpenAI / 其他）。**预设只是填空助手，不是白名单**——点一下把地址与常用模型填进输入框，用户仍可改成任意值。不做「拉取 /models 列表」：那需要后端代持 Key 去请求，与「Key 不进后端存储」冲突 |
| 预设选中态由值反推 | `matchPreset(baseUrl)` 比对地址得出当前高亮哪个预设，**不单独存「用户选了哪个服务商」**。存了会不一致：用户选了 DeepSeek 又把地址改成自己的中转，界面还显示 DeepSeek，会让人以为请求打到了官方 |
| **选服务商时「取 Key」必须立刻跟随草稿** | `activePreset` 的输入必须是 **`draftBaseUrl \|\| config.baseUrl`**（草稿优先），不能只看已保存的 `config`。旧实现从已保存配置反推：用户点「阿里云百炼」只改了草稿没保存 → `config` 仍为空 → `activePreset` 为 `null` → 回落到 DeepSeek 的取 Key 地址，用户看到「选了别的服务商但取 Key 还是 DeepSeek」（v0.1.6 真机反馈原话「api设置只有deepseek取key，选择其他服务商的时候不能对应」，E2E 回归 33e） |
| 没有 Key 页面就别给链接 | `keyUrl` 为空时（「其他 / 自建」以及手打的自建地址）渲染 `data-testid="byok-key-url-absent"` 的说明文字，**不渲染 `byok-key-url` 按钮**，也**不得用 `'https://platform.deepseek.com/api_keys'` 之类的默认值兜底**：给一个到不了的服务商页面比不给更差，用户会以为自己点错了 |
| **填了地址就必须填模型** | `llmConfigIssue()` 与后端 `_resolve_provider` 两处都拦。站点默认模型（如 `deepseek-flash`）在别家服务商那里不存在，发过去只会得到一句 `model not found`，用户还得猜是哪里错了。**不得为了「更宽松」去掉这条** |
| **填了地址就必须填自己的 Key** | 同一条规则的另一半，但严重程度高得多：Key 的回落目标是**站点自己的** Key。若允许「地址是我的、Key 用站点的」，后端就会拿站点凭据去请求用户指定的地址，凭据直接外泄；`/api/summarize` 无鉴权，任何人手写一个不带 `X-Vtt-Llm-Key` 的请求就能触发。所以「地址是我的」必须意味着「Key 也是我的」。前端 `llmConfigIssue()` 与后端 `_resolve_provider` 两处都拦，**任何一侧都不得单独放宽** |
| **只收 LLM Key** | 云端 ASR 用的是站点的免费模型（`XingChenAGI/XingChenASR-V3.2-Ultra` 等，刊例价 0 元），用户没有自备 Key 的必要，多一个输入框只是多一道门槛。后端仍保留 `X-Vtt-Asr-Key`（脚本 / API 调用方可用），**界面不得再收集 ASR Key**；面板文案必须说明「语音识别用的是免费模型，不需要填 Key」，否则用户会以为漏了功能 |
| 存储位置 | 只存 **localStorage**：`byokLlmKey` / `byokLlmBaseUrl` / `byokLlmModel` 三个键，**不写日志、不进 URL**。Key 不进请求体（只走请求头）；地址与模型**走请求体**（见下条）。不用 sessionStorage 的理由：桌面端与网页共用代码，关标签页就丢会让用户每次重开都要重填，实际没人会用 |
| **文案必须如实** | 面板底部 `data-testid="byok-storage-note"` 必须写清三件事：①只保存在本机浏览器 ②生成总结时 Key 会**随请求发给本站后端**，后端用它调用服务商、用完即弃 ③不写入任务记录、不写进日志。**严禁写「不会上传到服务器」**——Key 技术上一定经过服务器，部署者能看到，写「不上传」是误导 |
| 传输方式 | **Key 只走请求头** `X-Vtt-Llm-Key`，与 `backend/app/services/byok.py` 的 `HEADER_LLM_KEY` **必须逐字一致**。不走请求体：请求体会被错误日志、`payload=` 打印、体积限制一整套链路碰到。**地址与模型走请求体**（`llmBaseUrl` / `llmModel`）：它们不是秘密，放请求体才能让后端用 pydantic 校验并给出可读错误 |
| 默认掩码 | 已保存的 Key 只露末 4 位（`maskKey`）。设置面板常在投屏、录屏、截图场景下打开，明文铺在屏幕上等于泄露 |
| 本地校验边界 | `validateKeyFormat` 只拦手误（空 / 含空格换行 / 超长 / 过短）。**不校验 `sk-` 前缀**：各家格式不统一，猜错会把合法 Key 拒掉，比放过去更糟；也**不假装验证有效性**，那只有服务商能回答 |
| 能力门禁 | 总结相关的「未配置」禁用逻辑必须写成 **`站点未配 && 用户未填`** 才禁用。BYOK 上线后「站点没配」不再等于「用不了」，若仍按 `capabilities` 一刀切，用户刚填好 Key 却看到灰按钮（`src/hooks/use-byok-status.ts` 的 `useByokStatus`） |
| 变更通知 | 保存/清除后派发 `byok:changed` 事件（`notifyByokChanged()`），同页组件立刻重算门禁，不刷新。跨标签页由 `storage` 事件兜住 |
| 任意入口 | 结果页总结卡 / 首页提示都可以通过 `openApiSettings()`（派发 `api-settings:open`，`src/hooks/use-api-settings.ts`）打开面板。用 window 事件而不是 Context：`src/app/layout.tsx` 是服务端组件，塞 Provider 会把整棵树拖成客户端 |
| 错误归属 | 用户 Key 被 401/403 拒绝 → `BYOK_KEY_INVALID`（HTTP 401，标题「API Key 无效」，文案指向「API 设置」）；**用户自填的接口地址 / 模型名不合规 → `BYOK_ENDPOINT_INVALID`（HTTP 400，标题「接口地址不可用」，同样指向「API 设置」）**；站点 Key 被拒 → `SUMMARIZE_FAILED`（502）。三者绝不能混：前两者让用户改自己的配置，后者让用户找部署者，混了就是两边互相踢皮球。结果页的错误卡在这两种「用户自己的配置有问题」的码上，除「重试总结」外还必须给一个 `summary-open-api-settings` 入口——只让人重试等于让人白试 |
| **服务地址必须过 SSRF 校验** | 放行用户自填地址是为了让 BYOK 真的「支持任意 OpenAI 兼容服务商」，但**放行不等于放任**：`backend/app/services/byok.py` 的 `normalize_base_url` 必须拒绝非 http(s)、环回 / 私有 / 链路本地 / 保留地址（云厂商 metadata `169.254.169.254`）、`.local` / `.internal` 等内网域，以及 RFC 6598 共享地址段 `100.64.0.0/10`（阿里云 metadata 是 `100.100.100.200`，`ipaddress.is_private` 对它返回 False，必须单独拦）。**这条校验不得为了「支持自建 Ollama」而放宽**——自建场景应该由部署者改服务端 `LLM_PRESETS`，而不是让公网站点能访问内网。前端 `validateBaseUrlFormat` 只做格式层，**不能当作安全边界**（可绕过） |
| **SSRF 校验必须解析 DNS 后复检** | 只查字面量挡不住「域名指向内网」：`127.0.0.1.nip.io` / `localtest.me` 这类公共 DNS 服务能把任意 IP 编进域名，攻击者不需要自己的域名就能构造。`url_guard.resolves_to_blocked_ip()` 用 `socket.getaddrinfo` 解析后逐个 IP 复检，解析失败一律按不安全处理。**这一层不得为了省一次 DNS 开销去掉**。已知残余风险：DNS 重绑定（TOCTOU，检查时解析到公网、连接时解析到内网）理论上仍可绕过，彻底防御需固定解析结果再连接，成本远超范围 |
| **换服务商必须清总结缓存** | 缓存键是 `summary:<taskId>:<detail>`，**不含服务商**。保存或清除配置时必须调 `clearSummaryCache()`：不清的话，用户换到新服务商后点「重试总结」会命中旧缓存直接返回，页面显示一份总结，用户以为新配置通了，实际一次请求都没发——配置真错也会显示成功，比报错更难查 |
| 只对总结放行 | 云端 ASR 的地址仍固定为 `CLOUD_ASR_BASE_URL`，**不开放用户自填**。语音识别用站点的免费模型，界面既不收集 Key 也不收集地址 |
| 不降级 | ASR 用用户 Key 被拒时**不降级到本地引擎**。降级会跑出结果，用户以为云端成功了，从此不会去改那把已失效的 Key（`asr.py` 的 `except ByokKeyInvalidError: raise` 必须排在通用 `except` 之前） |
| 不落库 | Key 不写进任务库。`TranscribeJob.byok_key` 只随作业跨进程投递，`tasks._payload_dict` 里不得出现任何含 "key" 的字段 |

## 15. 桌面端自动更新（PR10 新增）

| 功能 | 锁定要求 |
| --- | --- |
| 启用条件 | 必须同时满足 **桌面壳 + 生产构建 + Tauri 桥存在**（`shouldEnableUpdater()` 与 `hasTauriBridge()` 两道判断）。**不得用 `IS_STATIC_SHELL` 决定**：它包含 Android，而 Capacitor 壳里根本没有 updater 插件，调下去只会抛「command not found」。网页端刷新即最新、Android 走 APK 覆盖安装，两者都不该出现这个入口 |
| 入口位置 | 顶栏 `data-testid="check-update"`，排在「API 设置」左侧。**网页 / 移动端构建里必须渲染为 0 个**（E2E 回归 30g 用 `toHaveCount(0)` 断言）。渲染一个点了什么都不会发生的按钮比不渲染更糟——用户会以为功能坏了 |
| 检查自动、下载手动 | 启动 8 秒后静默检查一次（`FIRST_CHECK_DELAY_MS`），之后每 4 小时复查（`RECHECK_INTERVAL_MS`）。**查到更新只提示、不自动下载**：安装包 3 MB 量级，国内到 GitHub 常要几十秒甚至超时，静默下载会在用户毫无察觉时占带宽，失败也无从得知。用户点「下载并安装」才走下载 |
| 自动检查失败必须静默 | 自动检查（`check(true)`）失败时状态回到 `idle`，界面不显示任何东西；**只有手动检查（`check(false)`）才写 `error` 并显示原因**。最常见的原因就是网络到不了 GitHub，弹出来只是噪音 |
| 手动检查失败要给出口 | `data-testid="update-error"` 的卡片必须说明「更新服务器在 GitHub 上，国内网络可能连不上」，并给出 `RELEASES_PAGE` 链接。**只报错不给出口等于把用户堵死**。对话框底部常驻「打开发布页」，任何状态下都能点 |
| 版本比较只能按数值 | `compareVersions` 拆成 `[major, minor, patch]` 逐位比。**禁止字符串比较**：`"0.1.10" > "0.1.9"` 字符串结果是 false，会把新版判成旧版，用户永远收不到更新（E2E 回归 30） |
| 解析失败一律「不比当前新」 | `parseVersion` 无法解析时返回 `null`，`compareVersions` 返回 0，`isNewerVersion` 返回 false。**宁可漏一次更新，也不要因为端点返回了奇怪的版本号就提示用户装一个坏包**（E2E 回归 30b） |
| 降级不算更新 | `isNewerVersion("0.1.3", "0.1.4") === false`。否则回滚发版时会让所有用户被提示「更新」到旧版。插件层 `check()` 本身也允许同版本，所以拿到 Update 后还要用 `getVersion()` 复检一次 |
| 进度总量未知时不猜 | `progressPercent(downloaded, total)` 在 `total` 缺失或 ≤0 时返回 `null`，界面切成不确定态。**不许用已下载字节数瞎算百分比**——跳来跳去的进度条比没有更让人困惑（E2E 回归 30d） |
| 签名不可绕过 | `tauri.conf.json` 的 `plugins.updater.pubkey` 是公钥，私钥离线保存（`D:/tauri-keys/vtt-updater.key`，**不入库**）。`bundle.createUpdaterArtifacts: true` 让 `tauri build` 产出 `.sig`。`scripts/make-latest-json.mjs` **在没有 `.sig` 时直接失败退出**——这是故意的：宁可发版失败，也不要发一个客户端装不上的更新。私钥丢失 = 已安装客户端永远收不到自动更新，必须离线备份 |
| `latest.json` 不入库 | 每次发版内容都不同（签名 + 时间戳），入库只制造无意义 diff。由 `release-desktop.yml` 现生成现上传，`.gitignore` 已忽略 `/latest.json` |
| 端点与下载页必须同仓库、且下载页不能用 `latest` | `UPDATE_ENDPOINT` 指向 `releases/latest/download/latest.json`（**这个必须带 `latest`**，那是 Tauri updater 的固定端点路径），而 `RELEASES_PAGE` 指向 `releases` **列表页**、不是 `releases/latest`，两者都在 `Ling-cn111/video-to-text`。指向不同仓库会出现「检查到更新但点进去是另一个项目」；用 `releases/latest` 则会被 GitHub 302 到**最后一个非 prerelease 的 tag**——新版本已经装上了但对应 Release 还没建时，用户点开看到的是上一个版本，观感就是「这个按钮根本没生效」（v0.1.6 真机反馈原话「0.16版本打开发布页还是0.15版本的」；E2E 回归 30f / 33 断言 `RELEASES_PAGE` 不含 `releases/latest`） |
| 下载页链接是已知限制 | 端点在 GitHub 上，国内可能不可达。这是当前方案的取舍，**不要为了「看起来更完整」把失败文案改成成功**。后续若要覆盖国内用户，需要把 `latest.json` 与安装包放到可达域名（如阿里云 FC 反代），属独立改造 |
| 发版必须三处版本号一致 | `package.json` / `src-tauri/tauri.conf.json` / `src-tauri/Cargo.toml`。`release-desktop.yml` 在**装依赖之前**就校验 tag 与两者一致（v0.1.4 曾漏改 Cargo.toml，构建日志显示 `Compiling app v0.1.3`，安装包内嵌版本与界面不一致）。不要等 20 分钟的 Rust 编译完才发现 |
| 资产名必须是 ASCII | `gh` 上传中文文件名会被剥成 `_`（v0.1.1 踩过）。workflow 里先把 `视频转文字_x.y.z_x64-setup.exe` 复制成 `video-to-text_x.y.z_x64-setup.exe` 再上传。产品名仍是「视频转文字」（`productName` 未变），用户无感 |

## 16. 移动端壳（Android，PR9 新增）

| 功能 | 锁定要求 |
| --- | --- |
| 构建入口 | `pnpm build:mobile` → `scripts/build-shell.mjs mobile`，与桌面端共用同一脚本（`desktop` / `mobile` 两个目标）。`next.config.mjs` 的静态导出条件是 `BUILD_TARGET === 'desktop' \|\| 'mobile'`。**不得为移动端另写一套导出逻辑**：桌面端已经踩过 `src/app/api` 的 POST Route Handler 在静态导出下不可用的坑，重写一遍必然重踩 |
| 资源必须打进安装包 | `capacitor.config.ts` 的 `webDir: 'out'`，`npx cap sync android` 把 `out/` 复制到 `android/app/src/main/assets/public/`。**`server.url` 绝不能配置**：配了就是壳里加载远程网页，资源又回到被墙的 `*.vercel.app`，等于白打包。验收必须含「飞行模式冷启动能看到首页」 |
| `androidScheme` 必须是 https | `server: { androidScheme: 'https' }`。WebView 里 `https://localhost` 属 secure context，`navigator.clipboard.writeText` 才可用；改成 `http` 会让 `src/lib/clipboard.ts` 的 Clipboard API 静默降级到 `execCommand`，**时间戳复制按钮会坏掉且不报错** |
| 环境变量必须显式注入 | `scripts/build-shell.mjs` 注入 `NEXT_PUBLIC_USE_MOCK=false`（缺省值是 Mock，不注入则 APK 走假数据）、`NEXT_PUBLIC_API_BASE`、`NEXT_PUBLIC_STATIC_SHELL=true`、`NEXT_PUBLIC_MOBILE_SHELL`。**`NEXT_PUBLIC_USE_MOCK` 是构建期常量**，漏注入无法在运行时补救 |
| 静态壳不注册 Service Worker | `src/lib/runtime.ts` 的 `IS_STATIC_SHELL` 为真时 `register-sw.tsx` 直接 return。asset 协议下 SW 行为未定义，注册失败会在控制台刷红字。旧的 `NEXT_PUBLIC_DESKTOP` 保留为兼容别名，**新代码一律用 `IS_STATIC_SHELL` / `IS_MOBILE_SHELL`** |
| 导出三分支降级 | `src/lib/share-file.ts` 的 `shareTextFile()`：**Capacitor Share（移动端主路径）→ `navigator.share({files})`（网页 / PWA）→ `blob:` + `<a download>`（桌面兜底）**。**主路径必须是原生插件**：Android WebView 的 Web Share API 依赖宿主实现，不可靠；`blob:` 下载在 WebView 里会变新窗口或静默失败。写文件用 `Directory.Cache`（系统可回收，不占用户存储） |
| 导出必须可等待 | `exportResult` / `exportSubtitles` 返回 `Promise<ShareOutcome>`，**不得退回 fire-and-forget 的 `void download(...)`**：旧实现在 WebView 里失败后用户点完什么也没发生、也没有任何提示，是移动端最典型的「看着能用实际不能用」 |
| 用户取消分享不是失败 | `ShareOutcome` 的 `cancelled` 分支**不得弹任何提示**（既不报错也不说「已下载」）。手机上误触取消是高频操作，当失败处理会让用户被红条轰炸。`failed` 才提示「导出失败，请重试或换一种格式」 |
| 成功文案分路径 | `shared` → 「已打开分享面板，选一个应用保存」；`downloaded` → 「已开始下载」。**两者不得共用一句**：手机上分享和下载是两件事，说错用户会去下载目录里找一个不存在的文件 |
| Android 物理返回键 | `src/hooks/use-android-back.ts` 通过 `@capacitor/app` 的 `backButton` 监听，决策交给纯函数 `resolveBackAction(pathname, canGoBack)`（`src/lib/back-navigation.ts`）：**非首页且有上一页 → `router.back()`；非首页但无上一页（冷启动直达 `/result`）→ `router.replace('/')`；已在首页 → 弹「再按一次返回键退出」，2 秒内再按才 `App.exitApp()`**。**禁止用 `history.state.idx` 判断**：那是 Pages Router 的字段，App Router 的 pushState 只写 `__NA` 与 `__PRIVATE_NEXTJS_INTERNALS_TREE`，`idx` 恒为 `undefined`，会让 `router.back()` 一次都不执行（v0.1.3 真机「按返回没反应」的根因）。**也不得只看 `canGoBack`**：结果页「返回首页」走 `router.push('/')`，历史变成 [首页, 结果页, 首页]，只看 `canGoBack` 会在首页按返回退回结果页。pathname 必须走 ref（effect 只依赖 `router`），否则每次跳转都重建监听器，跳转瞬间有真空期 |
| 安全区必须四边齐 | `src/app/globals.css` 的 `:root` 定义 `--safe-top/right/bottom/left`，取值 **`var(--safe-area-inset-X, env(safe-area-inset-X, 0px))`**（Capacitor 注入优先，浏览器 / iOS PWA 回落 `env()`）。**必须优先变量而不是直接用 `env()`**：Capacitor 走 `native` 模式时把变量写成 0px（原生 padding 已处理），而 `env()` 在该模式下仍可能返回非零（Chromium 按显示切口算，不看 view padding），直接用会顶两遍。**三个使用点缺一不可**：① `body` 四边 padding（内容整体让开）② 顶栏 sticky 的 `top: calc(var(--safe-top) + var(--backend-banner-offset))`——sticky 相对滚动视口定位，不吃 body 的 padding，不加就会一滚动滑到状态栏底下 ③ fixed 元素（不可达横幅的 `top`、sonner 的 `offset` / `mobileOffset`）。v0.1.3 只设了 left/right/bottom 三边、注释还写着「只有底部需要额外留白」，真机表现就是顶栏压住状态栏 |
| 只在移动端壳内启用 | 返回键监听与 Capacitor 插件 import 都在 `IS_MOBILE_SHELL` 判断之后，网页 / 桌面端不产生任何监听与动态 import（`@capacitor/*` 不会进初始包） |
| 图标与启动图 | `scripts/gen-android-icons.py` 从 `public/icons/icon-1024.png` 生成 27 个文件。**不用 `@capacitor/assets`**：它依赖 `sharp@0.32`，pnpm 的 build script 策略会让 win32-x64 二进制缺失，直接崩。自适应图标前景层铺满 108dp 画布（源图图案只占中央 43%，落在安全区内），底色 `#7C3AED` 与图标一致（裁切动画会露出这一层） |
| 签名密钥必须备份 | `android/keystore.properties` + `*.jks` 均已 gitignore，模板见 `keystore.properties.example`。**keystore 丢失 = 已安装用户永远无法升级**（Android 用签名证书判定「是不是同一个应用」），是唯一「做错没法补救」的环节。`versionCode` 已发布后不能回退 |
| 依赖镜像 | `android/build.gradle` 与 `android/gradle/init-mirrors.gradle` 把阿里云 Maven 镜像排在最前。**`init-mirrors.gradle` 不能删**：Capacitor 子工程的 `build.gradle` 自带 buildscript 且 `google()` 在最前，`pnpm install` 会覆盖对 node_modules 的修改，只能靠 `beforeProject` 追加。构建命令必须带 `--init-script gradle/init-mirrors.gradle` |

## 17. 系统分享接收与链接规范化（PR12 新增）

| 功能 | 锁定要求 |
| --- | --- |
| 只声明 SEND，不声明 BROWSABLE | `AndroidManifest.xml` 里只有一个额外 intent-filter：`android.intent.action.SEND` + `category.DEFAULT` + `mimeType="text/plain"`。**不得声明 `https://*.bilibili.com` 的 VIEW 过滤**：我们没有那些域名的所有权，过不了 App Links 的 `autoVerify`，于是用户每点一次 B站 链接都会弹「用哪个应用打开」——把一次便利变成长期打扰。分享意图只出现在用户主动点「分享」之后，没有这个问题（E2E 回归 32e 断言 manifest 里没有 `BROWSABLE`） |
| 插件注册必须在 `super.onCreate` 之前 | `MainActivity.onCreate` 先 `registerPlugin(ShareIntentPlugin.class)` 再 `super.onCreate(savedInstanceState)`。`BridgeActivity.onCreate` 内部会调 `load()`，而 `load()` 末尾会 `onNewIntent(getIntent())` 把**冷启动**的分享意图派发给插件；晚于 super 注册的话冷启动那一次分享直接丢掉（热启动不受影响，所以特别容易漏测） |
| 事件必须 retain | `notifyListeners(EVENT_SHARE, payload, true)` 第三个参数必须为 `true`。冷启动时 WebView 刚起、React 还没水合，JS 侧监听器必然晚于原生派发；`retainUntilConsumed` 会把参数暂存，等 JS 首次 `addListener` 时补发（`Plugin.sendRetainedArgumentsForEvent`）。**不要额外提供「主动拉取」接口**：补发 + 拉取会叠在一起，同一次分享提交两次任务 |
| 分享文案是整段文本 | 原生侧只把 `EXTRA_TEXT` 原样递给 JS，**不做任何 URL 猜测**。抽取在 `src/lib/share-intent.ts` 的 `sharedTextToVideoInput`：先 `extractVideoUrl` 抽链接，抽不到再试裸 BV/av 号，都不行返回 `null` 由调用方提示。`extractFirstUrl` / `extractVideoUrl` **都不能用 `\S+` 这类宽松正则**：B站 分享文案是「【标题】 看看这个 https://b23.tv/xxx 复制打开」，宽松正则会把中文一起吞掉（E2E 回归 32 / 32b） |
| 多条链接必须挑视频页 | `extractVideoUrl` 三档优先级：① 路径命中 `videoIdPatterns`（`/video/BV…`）② 域名命中平台（含 `b23.tv` 短链）③ 第一条链接。**第 1 档必须排在域名匹配之前**：`space.bilibili.com/123` 与 `www.bilibili.com/video/BV…` 都命中 `bilibili.com` 的 `hostPatterns`，只按域名挑会抽到 UP 主主页，后端解析主页当然失败，用户看到「视频不存在或链接有误」而链接抄得一字不差。`extractFirstUrl` 仍保留（固定取第一条），但 `resolveVideoUrl` 与分享链路**必须走 `extractVideoUrl`**（E2E 回归 33b / 33c） |
| 短链必须在后端展开 | `backend/app/services/short_link.py` 的 `expand_short_link` 逐跳跟随 `b23.tv` 重定向（**每一跳都过白名单**：只准 `b23.tv` 与 `bilibili.com` / `*.bilibili.com`，最多 3 跳、超时 8 秒、终点必须在 `/video/`），失败一律返 `None` 交回原链接。接入两处：`parser.py` 的 `parse_video_url`（展开后再走 view API，否则机房 IP 抓网页恒定 412 → 归到「视频不存在」）与 `transcribe.py` 的 `_resolve_target_url`（任务可能被脚本 / 重试 / 第三方直接创建，绕过 `/api/parse`）。**展开是优化路径，失败不能让用户看到报错**（后端 `tests/test_short_link.py`；E2E 回归 33d） |
| 认不出链接时不提交 | `useShareIntent` 在 `sharedTextToVideoInput` 返回 `null` 时**不调 `submitUrl`**，而是 toast 说明「没有在分享内容里找到视频链接」并回首页。分享来的可能是任意 text/plain（聊天记录、商品链接），静默失败或提交垃圾都会让用户困惑 |
| 同一条分享只处理一次 | `shouldIgnoreShare` 用「同文本 + `SHARE_DEDUPE_MS`（5 秒）窗口」去重。分享事件是 retain 的，若 effect 因 StrictMode 或路由切换重建监听器，补发与实时派发可能落进两个监听器，用户会看到「解析了两次」（E2E 回归 32d） |
| 送后端必须是 canonical 链接 | `submitUrl` 送的是 `canonicalUrlFor(video) ?? url`，不是用户原始输入。**原因**：B站 分享出来的是 `b23.tv` 短链，而后端 `subtitles.py` 与 `audio.py` 的 `_extract_bvid` 只认 `bilibili.com` 域名，短链会让「字幕快路径」与「playurl 音频快路径」双双失效，退回 yt-dlp 下载 + ASR —— 结果一样但慢一大截。`canonicalUrlFor` 认不出模板时返回 `null`，调用方保留原链接，**不许拼一个必然 404 的地址**（E2E 回归 32c）。注意短链末段（`b23.tv/AbCdEfG` 的 `AbCdEfG`）不是 BV 号，`canonicalUrlFor` 必然返回 `null`，这条链路真正靠的是后端 `short_link.expand_short_link` 展开（见上一条） |
| 只在移动端壳内启用 | 分享监听与 `@capacitor/core` 的动态 import 都在 `IS_MOBILE_SHELL` 判断之后，网页 / 桌面端不产生任何监听（E2E 回归 32f） |
| 分享后直接开转 | 用户从 B站 点「分享 → 视频转文字」，意图已经明确，直接 `submitUrl` + 跳进度页，**不再要求回首页确认一次**。这与首页表单的交互刻意不同：表单是「用户主动来粘贴」，需要确认；分享是「用户已经在别处确认过了」 |
| 产物里必须有原生插件与 JS 监听 | `node scripts/verify-shell.mjs mobile` 扫描 `out/` 的 JS 产物，断言含 `ShareIntent` 与分享失败提示文案。**只断言原生插件存在是不够的**：JS 侧不注册监听器的话，用户分享过来会落在首页什么都不发生，而构建日志全绿 |

## 18. 回归检查清单（改前端后必跑）

- [ ] 结果页默认显示「全文阅读」，文章排版无时间戳
- [ ] Tab 可切换到「时间戳定位」，列表 + 复制功能正常
- [ ] 导出 TXT / MD：内容零时间戳（`\d+:\d{2}` 正则零命中）、MD 含 `## 正文`
- [ ] 真实模式（`NEXT_PUBLIC_USE_MOCK=false`）：进度条按后端真实进度推进，无「演示模式」文案
- [ ] Mock 模式：示例链接走演示流程，演示文案恢复
- [ ] 失败任务 → 错误卡 + 重试可用
- [ ] 429 限流：显示「请求太频繁」+ 冷却提示，冷却期内「重试」禁用；**冷却秒数取自响应 `Retry-After`**（响应给 7 就显示约 7 秒，不给才退回 60 秒）（E2E 回归 1 / 4 / 4b）
- [ ] 首页转写模式选择器**只在能跑本地 ASR 的部署显示**；显示时默认「本地」，说明行常显且随选择变化（云端态含「上传至第三方」），Mock 模式下有无实际效果小字，真实模式无（E2E 锁定 4 在 Mock 下仍生效；E2E 回归 19 / 19b 管显示条件）
- [ ] 能力态：云端选项在未配置时禁用并有提示；总结区在没有可用 Key 时不显示红卡，而是「填写自己的 Key」引导（见下方 BYOK 与 §10 条目）
- [ ] 字幕来源任务结果页显示「来源：B站字幕」Badge；ASR 来源任务不显示
- [ ] 字幕分句：真实字幕稿经 `punctuate_subtitle_items` 后条数减半以上、每条有句末标点、时间戳单调不减且首条时间不变、文本中不含 `mm:ss`（后端 `test_punctuate_*` 共 11 个用例）
- [ ] AI 总结面板：keyPoints 至少一条带时间戳徽章，chapters 带时间区间（E2E 锁定 6）
- [ ] 总结失败降级：文字稿可见 + 错误卡与重试按钮；**summary 为 null 时导出 TXT/MD 仍与页面全文同源**（E2E 锁定 8）
- [ ] 首页模式标识：Mock 构建显示「演示模式 · Mock 数据」徽章与说明段；真实构建两者均不出现（E2E 锁定 7，CI 双模式各跑一次）
- [ ] 后端可达性横幅：不可达时出现且含 run.bat 指引，恢复后自动隐藏，可手动关闭，且不遮挡页面内容（E2E 锁定 9）
- [ ] 关闭「解析后自动 AI 总结」后解析任务：结果页无 keyPoints，显示引导 +「生成 AI 总结」按钮，点击后总结渲染（E2E 锁定 10）
- [ ] 结果页头部「复制全文」按钮可见：点击后剪贴板内容与全文阅读逐段一致且零时间戳，toast「已复制」反馈；剪贴板不可用时给出错误提示（E2E 锁定 11）
- [ ] 平台宣称：首页 Hero / 特性卡 / placeholder / metadata 里的平台名与 `SUPPORTED_PLATFORMS` 一致；真实构建不出现「抖音」（E2E 平台宣称 1-4，CI 双模式各跑一次）
- [ ] 导出字幕 SRT / VTT：SRT 时间码带逗号、VTT 首行 `WEBVTT` 且时间码带点号、结束时间晚于开始时间、文件名含「字幕」（E2E 回归 7 / 7b）
- [ ] 新增字幕导出后 TXT / MD 仍零时间戳、MD 仍含 `## 正文`（E2E 回归 7c，防两类导出互相污染）
- [ ] 阅读体验：章节目录可点击定位；搜索有命中/未命中文案且不改变正文 `innerText`；字号三档可切且刷新后保留（E2E 回归 8）
- [ ] 时间戳列表高度自适应：短稿不留大片空白、长稿仍可滚动（`max-h-` 加在 ScrollArea 视口上，E2E 回归 8b）
- [ ] 总结详细度：三档切换真的换一份结果（精简档要点条数更少），选择刷新后保留（E2E 回归 9 / 9b）
- [ ] 最近转写：首页列出本机最近任务，相对时间正确，点击可回到进度页或结果页；失败/中断的任务不出现在列表里（E2E 回归 4）
- [ ] 进度页：「不等了」按钮可回首页且提示任务会继续跑；页面不可见时暂停轮询、10 秒后降频（E2E 回归 5）
- [ ] 404 页：未知路径返回 404 且显示中文「页面不存在」，不出现 Next 默认英文文案，有返回首页链接（E2E 回归 10）
- [ ] 错误边界：`error.tsx` 与 `global-error.tsx` 的文案都在构建产物里（E2E 回归 10b，删文件即红）
- [ ] robots.txt / sitemap.xml 可达，robots 含 `Disallow: /processing` 与 `Disallow: /result`，sitemap 只含 1 个 `<loc>`（E2E 回归 11 / 11b）
- [ ] 安全响应头：CSP 含 `default-src 'self'` / `object-src 'none'` / `frame-ancestors 'none'`，`X-Frame-Options: DENY`，`X-Content-Type-Options: nosniff`，`Referrer-Policy: strict-origin-when-cross-origin`（E2E 回归 12）
- [ ] `/sw.js` 返回 `no-cache, no-store`，`/_next/static/*` 返回 `immutable` 长缓存（E2E 回归 12b）
- [ ] 深色模式：顶栏可切三档，`<html>` 正确增删 `.dark`，背景色真的变化，刷新后保留（E2E 回归 13）
- [ ] 深色可读性：深色态下 `body` 前景亮度高于背景；搜索结果高亮在深色下不刺眼（E2E 回归 13b）
- [ ] 改动根布局后 `public/sw.js` 的 `CACHE_NAME` 已提升（本次 v3 → v4）
- [ ] BYOK 面板：可从顶栏打开，保存后立刻掩码（明文不再出现在页面上），刷新后仍是掩码态，清除后回到输入框（E2E 回归 14 / 14b / 14c）
- [ ] BYOK 格式校验：含空格的值被拦下并给出原因，且没写进 localStorage（E2E 回归 14d）
- [ ] BYOK 传输：填了 Key 时请求头带上 `X-Vtt-Llm-Key`，**请求体里不得出现 Key**；没填时不发该头（不是发空串）（E2E 回归 15 / 15b）
- [ ] BYOK 错误归属：用户 Key 被拒显示「API Key 无效」并指向「API 设置」，文字稿不受影响（E2E 回归 16 / 16b）
- [ ] 未配置提示：AI 总结的公网分支提到「API 设置」且不暴露环境变量名；云端转写的公网分支**不得**提「API 设置」（界面不收集 ASR Key），只能建议改用本地模式（E2E 回归 17 / 21b）
- [ ] 无可用 LLM Key：结果页不出现「总结生成失败」红卡、不发 summarize 请求，而是引导态 +「填写自己的 Key」；填完 Key 后不用刷新即可生成（E2E 回归 18 / 18b / 18c）
- [ ] 转写模式选择器按部署显示：`localAsrAvailable=false` 时首页不渲染选择器与说明行；为 true 时仍默认本地（E2E 回归 19 / 19b）
- [ ] 设置面板只收 AI 总结 Key（无 ASR Key 输入框），且文案说明语音识别免填（E2E 回归 20）
- [ ] 面板存储说明如实：含「随请求发给本站后端」「不写入任务记录」，**不得出现「不会上传到服务器」**（E2E 回归 21）
- [ ] 站点未配 Key 但用户填了自己的：云端转写与 AI 总结都必须可用（后端 `test_byok.py` 的 `test_summarize_byok_works_without_site_key` / `test_transcribe_cloud_with_byok_key_passes_precheck`）
- [ ] 自定义服务商：面板三项（Key / 接口地址 / 模型名）都能填能存，刷新后保留；点预设按钮会把地址与模型填进输入框（E2E 回归 22 / 22b）
- [ ] 自定义服务商真的生效：填了地址与模型后，请求体带 `llmBaseUrl` / `llmModel`（不是发到站点预设），且 **Key 仍只在请求头、请求体里没有 Key**（E2E 回归 23）
- [ ] 只填地址不填模型：面板保存时被拦下并说明「还需要填写模型名」，不写进 localStorage（E2E 回归 23b）
- [ ] 只填地址不填 Key：同样被拦下，且后端 `_resolve_provider` 在拿到「用户地址 + 无用户 Key」时直接抛错，**绝不把站点 Key 发往用户指定的地址**（E2E 回归 23d / 23e + 后端 `test_summarize_rejects_custom_base_url_without_key` / `test_resolve_provider_never_pairs_site_key_with_user_base_url`）
- [ ] 内网地址被拦：`http://169.254.169.254/...` / `http://127.0.0.1/...` 这类地址后端返回 `BYOK_ENDPOINT_INVALID` 400（后端 `test_normalize_base_url_rejects_private_and_non_http` 共 9 个参数化用例 + `test_summarize_rejects_ssrf_base_url_at_http_layer`）
- [ ] 解析到内网的域名同样被拦：`127.0.0.1.nip.io` / `localtest.me` / `10.0.0.5.nip.io` / `100.100.100.200` 全部返回 400，而 `api.openai.com` / `api.deepseek.com` 正常放行（后端 `test_normalize_base_url_rejects_hostname_resolving_to_loopback` / `_private_ip` / `_when_dns_lookup_fails` / `_accepts_public_hostname` + `test_shared_address_space_is_blocked`）
- [ ] 换服务商后旧总结不被复用：保存或清除配置会清空 `summary:` 前缀的 sessionStorage，避免点「重试总结」命中旧缓存造成「假成功」（E2E 回归 25）
- [ ] 直接输入 AV/BV 号：`BV1xx411c7mD` / `av170001` 能直接解析并补成完整链接，小写 `bv` 也能识别且规范成大写；纯数字（抖音号）不被误认成 av 号（E2E 回归 24 / 24b / 24c）
- [ ] 移动端壳：`pnpm build:mobile` 后 `out/` 里 `NEXT_PUBLIC_USE_MOCK` 为 false、`IS_STATIC_SHELL` 与 `IS_MOBILE_SHELL` 均内联为 true（`node scripts/verify-shell.mjs mobile` 共 24 项）
- [ ] 导出改造未破坏桌面端：网页上导出仍触发下载并提示「已开始下载」（E2E 回归 27）
- [ ] 用户取消分享不报错、也不提示「已开始下载」（E2E 回归 27b）；分享成功提示「已打开分享面板，选一个应用保存」（E2E 回归 27c）
- [ ] 导出函数返回 Promise，不得退回 fire-and-forget（E2E 回归 26 / 26b）
- [ ] 真机验收（需要插线）：飞行模式冷启动能看到首页与示例链接、按物理返回键不会直接退出 App、导出能拉起系统分享面板并保存到手机、底部导航栏不遮挡「导出」按钮、**在 B站 App 点「分享」能看到「视频转文字」并直接开转**
- [ ] 返回键决策：非首页 + 有上一页 → 回退；非首页 + 无上一页 → 回首页；首页 → 二次确认后退出（E2E 回归 28，纯函数断言，覆盖 `pathname` 为 `null` 与空串两种边界）
- [ ] 安全区四边齐：注入 `--safe-area-inset-*` 后 `body` 四边 padding 全部生效、顶栏 `getBoundingClientRect().top >= 安全区顶部`、sticky 的 `top` 含安全区（E2E 回归 29）
- [ ] 不可达横幅也让开状态栏（fixed 定位不吃 body 的 padding，E2E 回归 29b）
- [ ] 自动更新入口：桌面端构建里有「检查更新」按钮，网页 / 移动端构建里 **0 个**（`node scripts/verify-shell.mjs desktop` 20 项 + E2E 回归 30g）
- [ ] 版本比较：`0.1.10 > 0.1.9`、降级不算更新、解析失败不比当前新（E2E 回归 30 / 30b）
- [ ] 更新只在桌面壳 + 生产构建下启用（E2E 回归 30c）
- [ ] 下载进度总量未知时返回 `null` 而不是瞎猜百分比（E2E 回归 30d）
- [ ] 桌面端「打开发布页」是 button 而非 `<a target=_blank>`（WebView2 会静默吞掉新窗口，表现为「点了没反应」；`verify-shell.mjs desktop` 断言对话框内 `target=_blank` 数量为 0）
- [ ] 手机端检查更新入口在**设置面板**里（`mobile-update` 区块 + 当前版本号 + `mobile-check-update` 是 button），顶栏不渲染桌面端入口（`verify-shell.mjs mobile` 24 项）
- [ ] 分享文案能抽出链接：`"【标题】 看看这个 https://b23.tv/AbCdEfG 复制打开"` → `https://b23.tv/AbCdEfG`；裸号补成完整链接；认不出返回 `null` 不瞎猜（E2E 回归 32）
- [ ] 抽链接不吞中文、不带尾随标点，尾随文字不会被百分号编码进 path（E2E 回归 32b）
- [ ] 送后端的是 canonical 链接：`b23.tv` 短链按 videoId 还原成 `https://www.bilibili.com/video/<id>`，认不出形态返回 `null` 保留原链接（E2E 回归 32c）
- [ ] 同一条分享 5 秒内不重复处理，不同文本一律处理，空文本不提交（E2E 回归 32d）
- [ ] Android manifest 只声明 `SEND` + `text/plain`，**没有** `BROWSABLE`；`MainActivity` 在 `super.onCreate` 之前 `registerPlugin`；插件用 `notifyListeners(..., true)`（E2E 回归 32e）
- [ ] 移动端产物里含 `ShareIntent` 与分享失败提示文案（`verify-shell.mjs mobile` 扫描 `out/` 的 JS，证明监听器真的被打包）
- [ ] 发布页指向 `releases` 列表页、**不含** `releases/latest`（`latest` 会被 GitHub 302 到最后一个非 prerelease 的 tag，新包配旧页面看起来像按钮没生效；E2E 回归 30f / 33）
- [ ] 分享文案里有多条链接时优先挑出路径含 `/video/` 的那条，`space.bilibili.com/123` 这类主页不能被选中；一条都不认得时仍返回第一条（E2E 回归 33b / 33c）
- [ ] `resolveVideoUrl` 与 `sharedTextToVideoInput` 都走 `extractVideoUrl`，不得退回 `extractFirstUrl`（E2E 回归 33c）
- [ ] 短链末段不是 BV 号时 `canonicalUrlFor` 返回 `null`、保留原短链交给后端展开，**不许拼 `https://www.bilibili.com/video/<短链末段>`**（E2E 回归 33d）
- [ ] 后端短链展开：只认 `b23.tv`、每一跳过白名单、终点必须含 `/video/`、任何异常返回 `None` 交回原链接；`parse_video_url` 与 `_resolve_target_url` 两处都接入（后端 `tests/test_short_link.py` 11 项）
- [ ] 「取 Key」跟随草稿里的服务商，不等保存；`keyUrl` 为空时渲染 `byok-key-url-absent` 而不是死链（E2E 回归 33e + `verify-shell.mjs mobile` 的「API 设置」文字可见两项）
- [ ] `latest.json` 的签名能解出 `version:` 与安装包文件名，URL 与 tag 一致（发版前用 `make-latest-json.mjs` 的输出核对）
- [ ] 发版前三处版本号一致：`package.json` / `src-tauri/tauri.conf.json` / `src-tauri/Cargo.toml`（`release-desktop.yml` 会拦）
- [ ] `pnpm lint && pnpm build` 通过

> 修改本文件（新增/放宽锁定项）需要用户明确确认。
