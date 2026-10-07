# 阶段三验收报告：真实 AI 总结（LLM）

- **验收日期**：2026-10-07
- **分支**：feat/backend-summarize（PR #28）+ chore/stage3-closeout（本报告与文档收尾）
- **范围**：阶段三 = 真实 LLM 总结（任务 E）；含前置任务 D（云端 ASR 手动切换）、任务 C（参考稿全文人工校对→绝对 CER 基准）、任务 G（B站字幕快路径激活，替代任务 F 的 Cookie 方案）

## 一、交付内容

| 项 | 说明 |
| --- | --- |
| `POST /api/summarize` | 无状态：请求 `{transcript, title, duration}` → `{summary, keyPoints:[{time,text}], chapters:[{title,timeStart,timeEnd,note}]}`（chapters 字段名 `note`，避免与顶层 summary 混淆） |
| LLM 接入 | `LLM_PROVIDER=deepseek\|qwen`（deepseek-flash / qwen-plus，OpenAI 兼容）；`response_format=json_object` + 解析失败重试×2；未配 Key 提交即 400 `SUMMARIZE_NOT_CONFIGURED`；文字稿超 10 万字报 413 `TRANSCRIPT_TOO_LONG` |
| keyPoints 时间戳三重保障 | ① 输入文字稿格式化 `[mm:ss] text`；② prompt 明确禁止编造；③ 后端就近吸附校验（单测覆盖编造场景 45s→30s / 379.2s→380s） |
| 长文 chunking | 超 6000 字按条目边界切块（条目不拆），逐块提取要点 → 合并终稿；时间戳全程指向原 transcript |
| 成本记录 | 每次调用 usage tokens × 单价（官方人民币口径核对）→ 日志；实测见 [docs/llm-cost.md](llm-cost.md) |
| 前端切换 | `real-api.summarize` → `/backend-api/summarize`；Summary 契约变更同步 9 个文件（types / mock-data ×2 / Mock Route Handler / summary-card / article.ts 同源分段 / http+api / task-store）；keyPoints 渲染带时间戳徽章、章节带 `mm:ss–mm:ss` 区间 |
| 锁定协议 | `frontend-features.md` §1 AI 总结面板更新 + §4 切换承诺兑现；**E2E 锁定 6**（破坏验证✓）；Tab / 导出 / 进度页零触碰 |

## 二、真实链路验收（27 分 18 秒视频）

**链路**：字幕快路径（dm/view AI 字幕 810 句 / 7111 字，零成本）→ deepseek-flash 总结（3 块 + 合并，4 次调用）。

**质量示例（LLM 实际输出节选）**——视频[《反向旅游 陕西铜川》](https://www.bilibili.com/video/BV1QuHx6mE4e)：

> **一句话概要**：作者以"反向旅游"探访陕西 GDP 垫底的铜川，按煤、瓷、仿制兵马俑、串与咸汤面的清单拍摄：从空无一人的秦人村落、耀州窑四绝与唐代盖罐，到王石洼煤矿遗址，讲述资源枯竭型城市的辉煌、阵痛与转型倔强。
>
> **要点**（5 条，全部带真实时间戳）：[00:01] 反向旅游选定铜川… / [00:18] 中药熬制的咸汤面与空城秦人村落… / [13:14] 耀州窑四绝与唐代黑釉塔式盖罐… / [18:17] 王石洼煤矿遗址… / [19:51] 2019 年列为资源枯竭型城市…
>
> **章节**（4 段覆盖全片）：[00:00–10:44] 反向旅游开局 / [10:44–13:14] 徒步下山与补给 / [13:14–18:17] 铜川的瓷 / [18:17–27:04] 铜川的煤与城市转型

**成本**（官方人民币单价，空闲 cache-miss；用户截图核对）：

| 项 | 数值 |
| --- | --- |
| token 用量 | 输入 12,184 + 输出 14,627 |
| 单视频总结成本 | **¥0.0706**（≈ 7 分钱） |
| 总结耗时 | 32.7-69.4s |
| **全流程（解析 0 + 字幕转写 0 + 总结）** | **≈ ¥0.07，LLM 为唯一成本项** |

**输出 token 结构核实**（用户质询）：deepseek-flash 为推理模型，输出 token 大部分为思维链
（探针实测 completion 中 81% 为 `reasoning_tokens`），非内容冗余；合并调用输入无重复。
后续优化方向记录于 [llm-cost.md](llm-cost.md)（关闭思维链可省 60-80% 输出，当前不做）。

## 三、阶段三前置任务的验收锚点（引用）

- **任务 D（云端 ASR）**：手动切换 + 未配 Key 明确报错 + 降级链；PR #23/#24
- **任务 C（绝对 CER 基准）**：科普/讲解参考稿全文人工校对；Qwen3 云端平均 9.03%（讲解 14.60% ≤ 15% 达标）；PR #27
- **任务 G（字幕快路径）**：`/x/v2/dm/view` 未登录 AI 字幕，四层降级 + 来源 Badge + E2E 锁定 5；PR #26

## 四、测试与回归

- backend pytest **74 passed**（含 summarizer 15 mock：编造时间戳 / 超长 / 30 分钟级多块合并 / 重试 / provider / HTTP 层；1 条 network 真实集成本地 5.3s 通过）
- `pnpm lint` / Mock 构建 ✅；Playwright E2E **6/6**（锁定 1-6，其中锁定 6 破坏验证✓）
- CI 三 job 全绿（PR #28）

## 五、遗留问题

| # | 问题 | 级别 | 去向 |
| --- | --- | --- | --- |
| 1 | 任务注册表仍为进程内存（多 worker 需 Redis） | 边界 | 沿袭阶段二遗留 #3 |
| 2 | deepseek-flash 为推理模型：输出 token 含思维链（81%），按输出价计费 | 优化项 | 关闭/缩短思维链可降成本 60-80%，绝对值小暂不做（见 llm-cost.md） |
| 3 | qwen-plus 单价未按阿里云百炼核对（deepseek 已核对） | 边界 | 启用 qwen provider 前核对 |
| 4 | 章节合并质量依赖块边界（按条目数切块非语义切段） | 边界 | 实测 4 章节覆盖良好；如遇差例再做语义切块 |
| 5 | 总结面板无手动「重新生成」入口 | 体验 | 可与 M3 本地视频一并规划 |

## 六、结论

**通过**。阶段三闭环后，产品核心链路完整：解析 → 字幕快路径（主路径，5-7 秒零成本）→ 本地/云端 ASR 兜底（绝对 CER 4-44%，按场景选型）→ 真实 LLM 总结（单视频 ¥0.07）。后续 M2 多平台 / M3 本地视频 / M4 打包未启动，规划见 [ROADMAP.md](ROADMAP.md)。
