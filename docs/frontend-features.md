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
| 时间戳定位视图 | 逐句列表，句首时间戳可点击复制（`[mm:ss] 文本`），带复制成功反馈；Badge「点击时间可复制」保留 |
| AI 总结面板 | 右侧独立卡片：一句话概要、**3-5 条要点（每条带原文字稿时间戳徽章，`data-testid="key-points"`）**、章节笔记（**时间区间 `mm:ss–mm:ss`，`data-testid="chapter-timeline"`**，字段 `{title, timeStart, timeEnd, note}`）。**修改总结 UI 时严禁波及左侧 Tab 结构**。阶段三起由真实 LLM 生成（E2E 锁定 6；失败降级见下条） |
| 总结失败降级（任务 M2-P0） | 总结失败**不得拖垮整页**：文字稿/全文阅读/导出照常可用，仅总结区显示错误卡（`data-testid="summary-error"`）+「重试总结」按钮；加载中为骨架（`summary-skeleton`）；未总结态为引导文案 +「生成 AI 总结」按钮（`summary-manual-prompt` / `manual-summarize-button`，任务 M2-P3 由原空态 `summary-empty` 升级而来，手动生成与错误重试共用 `retrySummary` 逻辑） |
| 刷新恢复 | 直接刷新 / 直链访问按 taskId 从接口恢复，不白屏 |

## 2. 导出（无时间戳铁律）

| 功能 | 锁定要求 |
| --- | --- |
| TXT 导出 | `《标题》` + `来源：xx ｜ 时长：x 分 x 秒`（中文时长，**严禁 12:34 形式的类时间戳写法**）+ 纯文本段落。**全文件零时间戳** |
| Markdown 导出 | `# 标题` + `> 元信息` + **`## 正文`** + 段落（空行分隔）。**全文件零时间戳**，可直接贴入 Notion / Obsidian |
| 导出入口 | 结果页头部「导出」下拉菜单（TXT / MD），菜单带「纯文本 · 不含时间戳」说明 |
| 分段来源 | 导出段落 = 页面全文视图段落（`formatTranscriptToArticle`），两者必须保持一致 |

## 3. 转写进度页（/processing/[taskId]）

| 功能 | 锁定要求 |
| --- | --- |
| 真实进度 | 进度条与百分比来自后端任务接口的真实进度（阶段锚点：parse_link 5% → extract_audio 15-35% → asr 40-95%），**不得改回 Mock 的固定 10 秒推算**（Mock 模式除外） |
| 四阶段步进 | 解析链接 → 提取音频 → 语音识别 → 生成总结，完成打勾 / 进行中脉冲 |
| 失败重试 | 任务失败显示友好中文错误 + 重试按钮（重建任务）+ 返回首页 |
| 任务中断 | 服务重启后未完成任务（后端 `status=interrupted`）显示「任务已中断（服务重启过），请重试」+ 重试按钮（复用失败错误卡路径，任务 H） |
| 演示文案 | 「演示模式：转写全程约 10 秒」提示**仅 Mock 模式显示**（真实模式严禁出现） |

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
| PWA | manifest + Service Worker（仅生产注册），顶栏安装按钮 |
| 全文导出一致性 | 页面全文视图、TXT、MD 三者的段落必须同源（`formatTranscriptToArticle`），改分段逻辑必须三处一起验证 |

## 6. 转写模式选择（首页，任务 D 新增）

| 功能 | 锁定要求 |
| --- | --- |
| 默认本地 | 首页「转写模式」选择器默认选中**本地**（faster-whisper small），不得更改默认值 |
| 模式说明常显（M2-P2-B） | 选择器下方常显说明行 `data-testid="mode-description"`，文案随选择变化：本地「本地模式：免费 · 离线 · 较慢，准确率中等」/ 云端「云端模式：更快 · 准确率更高 · 音频将上传至第三方服务」（云端态 amber + 盾牌图标，**隐私提示已并入本行**）。该说明行不得移除、不得改为仅悬停提示 |
| 云端能力态（M2-P2-A） | 能力探测（null=检测中）时云端选项禁用并显示「云端模式检测中…」；`cloudAsrConfigured=false` 时禁用并显示配置引导；本地选项始终可用 |
| Mock 模式小字 | Mock 模式下选择器下方显示「当前为 Mock 演示模式，此选择无实际效果」小字；**真实模式严禁出现该提示**（由 `NEXT_PUBLIC_USE_MOCK` 控制渲染） |
| 请求透传 | `engine`（local/cloud）随 `POST /api/transcribe` 提交，失败重试沿用同一模式；Mock 模式不发送 |
| 纯手动 | 引擎选择纯手动，禁止加入任何基于 VAD / 信噪比 / 时长的自动切换逻辑 |

## 7. 文字稿来源标识（任务 G 新增）

| 功能 | 锁定要求 |
| --- | --- |
| 字幕快路径优先 | 有字幕的视频优先走字幕快路径（降级链：yt-dlp CC → dm/view AI → 预留 Cookie 层 → ASR），后端标记 `transcriptSource`（subtitle_cc / subtitle_ai / asr），降级链不得移除 |
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
| 探测节奏 | 应用加载即探测一次，之后每 15 秒一次；单次 3 秒超时。失败 / 超时 / 非 2xx 一律判定不可达 |
| 横幅 | `data-testid="backend-unreachable-banner"`，`role="alert"`，文案含「后端服务不可达」与「run.bat start」；固定顶部（fixed + 等高占位） |
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
| 未配置 LLM Key | `summarizeConfigured=false` 时手动按钮禁用并显示「未配置 LLM Key，无法生成总结」；自动总结路径保持既有错误卡引导（M2-P2-A） |
| 纯前端控制 | 开关不进入任何接口请求体，`/api/transcribe`、`/api/summarize` 契约不变 |

## 11. 回归检查清单（改前端后必跑）

- [ ] 结果页默认显示「全文阅读」，文章排版无时间戳
- [ ] Tab 可切换到「时间戳定位」，列表 + 复制功能正常
- [ ] 导出 TXT / MD：内容零时间戳（`\d+:\d{2}` 正则零命中）、MD 含 `## 正文`
- [ ] 真实模式（`NEXT_PUBLIC_USE_MOCK=false`）：进度条按后端真实进度推进，无「演示模式」文案
- [ ] Mock 模式：示例链接走演示流程，演示文案恢复
- [ ] 失败任务 → 错误卡 + 重试可用
- [ ] 首页转写模式默认「本地」；**说明行常显且随选择变化**（云端态含「上传至第三方」）；Mock 模式下有无实际效果小字，真实模式无（E2E 锁定 4）
- [ ] 能力态：云端选项在检测中/未配置时禁用并有提示；总结区在未配置 LLM Key 时显示引导（关闭自动总结时为手动按钮禁用 + 配置引导）（手动验收，见 PR 步骤）
- [ ] 字幕来源任务结果页显示「来源：B站字幕」Badge；ASR 来源任务不显示
- [ ] AI 总结面板：keyPoints 至少一条带时间戳徽章，chapters 带时间区间（E2E 锁定 6）
- [ ] 总结失败降级：文字稿可见 + 错误卡与重试按钮；**summary 为 null 时导出 TXT/MD 仍与页面全文同源**（E2E 锁定 8）
- [ ] 首页模式标识：Mock 构建显示「演示模式 · Mock 数据」徽章与说明段；真实构建两者均不出现（E2E 锁定 7，CI 双模式各跑一次）
- [ ] 后端可达性横幅：不可达时出现且含 run.bat 指引，恢复后自动隐藏，可手动关闭，且不遮挡页面内容（E2E 锁定 9）
- [ ] 关闭「解析后自动 AI 总结」后解析任务：结果页无 keyPoints，显示引导 +「生成 AI 总结」按钮，点击后总结渲染（E2E 锁定 10）
- [ ] `pnpm lint && pnpm build` 通过

> 修改本文件（新增/放宽锁定项）需要用户明确确认。
