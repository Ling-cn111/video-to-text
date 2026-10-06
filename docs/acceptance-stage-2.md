# 阶段二验收报告：音频提取 + 字幕优先 + ASR 真实转写

- **验收日期**：2026-10-03
- **验收人**：技术负责人（AI 辅助执行，全程真实运行取证）
- **代码分支**：阶段二功能代码经 [PR #14](https://github.com/Ling-cn111/video-to-text/pull/14) 合入 `main`（原 `feat/backend-transcribe` 分支按仓库 delete_branch_on_merge 设置在合并后自动删除）；本验收的收尾改动在 `feat/stage-2-acceptance` 分支进行
- **结论**：**通过**（1 项范围外说明 + 2 项已知边界，详见「遗留问题」）

---

## 一、验收环境

| 项 | 值 |
| --- | --- |
| OS | Windows 11（10.0.26200） |
| Python / Node | 3.13.5（venv）/ v24.15.0（pnpm 12.5.1） |
| 后端 | FastAPI + uvicorn（`app.main:app`，端口 8000），yt-dlp 2026.8.19，faster-whisper 1.2.1，FFmpeg 9.0.2（winget Gyan.FFmpeg，验收中安装） |
| 前端 | Next.js 14.2.35（生产构建 `pnpm start`），`NEXT_PUBLIC_USE_MOCK=false`，`BACKEND_ORIGIN=http://localhost:8000` |
| 测试视频 | ① 无字幕：`av116187438517161`（135 秒科普视频，实测无 CC/AI 字幕）② 纯音乐 MV：`BV1GJ411x7h7`（212 秒，VAD 边界回归用） |

## 二、核心功能验收

### 2.1 有字幕路径（字幕快路径）

**结果：受限通过，代码正确性已证明，真实 B站字幕路径需 Cookie 后补验。**

实测事实：未配置 Cookie 时，yt-dlp 对常规 B站视频（含实测的 5+ 个视频）拿不到 CC/AI 字幕——B站 AI 字幕的播放器接口需登录（详见四.2）。多轮搜索探测亦被 B站搜索接口限流（HTTP 412）。

因此字幕快路径以下列**真实运行证据**证明其正确性（pytest，全量通过）：

- B站 CC 字幕 json / json3 / WebVTT 三种真实格式的解析单测（含坏数据容错）
- 注入式 HTTP 集成测试：POST 创建任务 → BackgroundTasks 执行字幕快路径 → GET 返回完整 transcript 与元数据透传，**秒级完成（0.4s 测试耗时）**

字幕路径对真实 AI 字幕生效的端到端验证，在 `BILI_COOKIE` 支持实现后补做（已列入 backend/.env.example 文档与遗留问题）。

> **✅ 已补验并升级（2026-10-06，任务 G，PR：feat/bili-player-api-subtitle）**：发现 B站**弹幕元数据接口
> `/x/v2/dm/view?aid=&oid=&type=1` 未登录即可返回 AI 字幕轨道**（无需 Cookie / wbi 签名；player/v2 与
> wbi/v2 未登录恒为空，18 组合矩阵实证），字幕快路径改造为分层降级（yt-dlp CC → dm/view AI →
> 预留 BILI_COOKIE 层 → ASR），来源标记 `transcriptSource`（subtitle_cc / subtitle_ai / asr）随任务返回，
> 前端结果页显示「来源：B站字幕」Badge（E2E 锁定 5）。真实端到端已验证：BV1S6aB63Er7 未登录全链路
> 6.8s 拿到 171 条带时间戳字幕（含原验收用例 av116187438517161 在内的 6 视频中 5 个有 AI 字幕轨道）。
> **注意：本节原「AI 字幕的播放器接口需登录」的实测结论已被修正**——需登录的只是 player API，
> 弹幕元数据接口不受限。侦察证据链见 backend/docs/bili-subtitle-recon.md。

### 2.2 无字幕路径（音频下载 → FFmpeg → faster-whisper）✅

以无字幕的 135 秒科普视频走 HTTP 全链路（`POST /backend-api/transcribe` → 轮询）：

```
[1] processing parse_link 5%
[2] processing asr 40%
[4] processing asr 54%
[5] processing asr 65%
[6] processing asr 76%
[7] completed asr 100%
```

- 阶段推进真实（下载/转换在两次轮询间完成，FFmpeg 16kHz 单声道转换生效——`check_ffmpeg()=True` 且未触发降级分支）
- transcript（节选，前端 UTF-8 显示正常）：`[{time:7.31,"text":"本视频只在科普基础知识"},{time:9.87,"text":"帮助公众提升健康认知"},{time:12.11,"text":"严格规避任何涉及低俗"},{time:14.35,"text":"善情或擦边球的表述"}...]`，共 57 段
- 转写总耗时约 21 秒（135 秒音频，base/int8 CPU）

### 2.3 前端联调 ✅

浏览器完整走「首页 → 解析 → 转写进度 → 结果页」（截图存档于验收会话）：

- 解析页/进度页/结果页显示**真实标题、真实封面、真实时长 02:15**，无 Mock 数据残留（无「深度实测」演示数据、无「演示模式」提示——本轮修复：该提示改为仅 Mock 模式显示）
- **前端进度条按真实进度更新**（浏览器 DOM 采样）：`解析链接 5% → 语音识别 40% → 54% → 65% → 76% → 88% → 跳转结果页`
- 结果页渲染 **57 段真实文字稿**（时间戳 00:19-01:32，文案与视频实际口播一致）

### 2.3 补记（验收后修复回测，PR：fix/stage2-final-ui-and-export）

初版本报告遗漏了当时已完成的两项前端修复记录，此处补全（均为真实浏览器点击取证）：

- **全文阅读 / 时间戳定位双视图**：结果页文字稿区为 Tab 切换，**默认进入「全文阅读」**（无时间戳文章排版，真实转写 625 字按语义分段）、「时间戳定位」列表（真实时间戳 00:07-01:30，点击时间可复制）——两个 Tab 均实测渲染正确（截图存档）
- **无时间戳导出**：真实点击导出菜单，探针捕获文件全文——TXT（671 字节）与 Markdown（680 字节，含 `# 标题` / `> 元信息` / `## 正文` 结构）**均零时间戳**（`\d+:\d{2}` 正则零命中），可直接贴入 Notion / Obsidian
- 根因与修复过程见 PR（fix: restore text view, fix export format, and improve ASR accuracy）：PR#11 成果未进入 main 导致回退，已从集成分支精确恢复并升级为 Tab 布局
- 前端功能锁定清单见 [frontend-features.md](frontend-features.md)（防止再次回退）

## 三、「三个真问题」回归测试 ✅

### 3.1 PyAV 兼容性（自定义解码生效）

运行日志摘要（完整输出见验收执行记录）：

```
[证据1a] PyAV 自定义解码成功：2172570 采样点 @16kHz 单声道 float32，时长 135.8s，dtype=float32
[证据1b] 数组直接喂 model.transcribe 成功：57 段（绕开 av.open(metadata_errors=...) 内部解码）
[证据1c] 对照：直接传文件路径会触发 PyAV 19 不兼容 —— TypeError: open() got an unexpected
         keyword argument 'metadata_errors'
```

### 3.2 Silero VAD 边界（纯音乐 MV 自动去 VAD 重试）

```
[证据2a] VAD 开启：0 段（Silero 把纯音乐全部过滤）
[证据2b] VAD 关闭：56 段（正常识别歌词）
[证据2c] 流水线最终（VAD 失败自动去 VAD 重试）：status=completed，段数=90（不为 0 ✓）
```

### 3.3 接口清理（无 Mock 残留）✅

- 代码层：`grep` 全部前端请求路径——`real-api.ts` 的 parse / transcribe(POST) / getTask(GET) 均指向 `/backend-api/*`（经 Next rewrites 服务端代理到 FastAPI）；仓库内仅 Mock 客户端（`NEXT_PUBLIC_USE_MOCK !== 'false'` 时启用）包含 `/api/transcribe` 调用
- **浏览器 Network 面板等价证据**（`performance` 资源清单，一次完整联调）：

```
/backend-api/parse
/backend-api/transcribe
/backend-api/transcribe/35d1b0cbff4a437b9b451dc947d1d1c8
/api/summarize        ← 唯一 Mock：AI 总结属阶段三范围（见遗留问题）
```

## 四、「两个已知边界」确认 ✅

1. **任务注册表**：单进程 uvicorn 下任务创建/轮询/进度更新全部正常（2.2 全流程即证）。`services/tasks.py` 封装了全部存储访问（`grep` 确认 `_TASKS` 无外部直接引用），业务代码只依赖 `create_task / get_task / update_task` 三个函数——替换 Redis 不需要改路由与流水线。backend/README.md 已明确写出该边界与替换方式。
2. **B站 AI 字幕 Cookie**：未配置 `BILI_COOKIE` 时实测优雅降级（2.2 全流程即「无字幕 → ASR」路径）。`BILI_COOKIE` 的作用与获取方式已补充至 `backend/.env.example` 与 `backend/README.md`，供后续启用字幕快路径。

## 五、测试与 CI ✅

- `pytest`：**21 passed**（11 解析/守卫 + 10 转写，含真实 ASR 集成；CI 跳过 `network` 标记）
- `pnpm lint`：✔ No ESLint warnings or errors；`pnpm build`：✔（9/9 静态页）
- GitHub Actions：PR #14 双 job 绿灯（lint & build + backend tests）

## 六、发现的遗留问题

| # | 问题 | 级别 | 去向 |
| --- | --- | --- | --- |
| 1 | AI 总结仍为 Mock（`/api/summarize` 为前端同域 Route Handler） | 范围外 | 阶段三接真实 LLM |
| 2 | ~~B站 AI 字幕需 `BILI_COOKIE`，未实现读取逻辑（仅文档预留）~~ | **已修复 + 端到端验证（无需 Cookie）**（PR：feat/bili-player-api-subtitle，任务 G） | 发现弹幕元数据接口 `/x/v2/dm/view` 未登录可取 AI 字幕（零 Cookie 零签名），字幕快路径升级为分层降级（yt-dlp CC → dm/view AI → 预留 Cookie 层 → ASR），`transcriptSource` 来源标记 + 前端来源 Badge + E2E 锁定 5；真实未登录链路 6.8s / 171 条字幕实测通过（详见 2.1 补记与 backend/docs/bili-subtitle-recon.md）。`BILI_COOKIE` 降级为预留兜底（未实现） |
| 3 | 任务注册表为进程内存：重启后任务丢失（前端刷新会提示任务不存在并可重试） | 边界 | 多 worker 部署前换 Redis |
| 4 | ~~whisper base 模型对专业词汇/英文歌词有识别误差~~ | **已修复 + 量化验证 + 引擎横向对比**（PR：fix/stage2-accuracy-and-ui → fix/asr-accuracy → fix/asr-engine-swap） | 第一轮：`initial_prompt`（标题）注入、轻量文本后处理、模型建议文档化。第二轮（CER 专项）：建立可量化基准（3 视频 × 4 模型，docs/asr-benchmark.md），**默认模型 base → small**（平均 CER 55.72% → 40.39%；清晰口播类 16.20% → 6.29%，降幅 61%）；zhconv 繁→简；热词 `hotwords` 经 initial_prompt 注入；ASREngine 抽象 + CloudASREngine（`ASR_ENGINE=cloud`）作为强 BGM 音频兜底。第三轮（引擎横向对比）：Qwen3-ASR-1.7B 平均 48.93%（教程类 5.35% 最优，但 CPU 耗时 10-18 倍）；Fun-ASR-Nano 幻觉严重不可用（平均 90.33%）；Demucs 人声分离对幻觉型场景无效（85.25%）——**最终结论：默认保持 faster-whisper small（40.39%），强 BGM 场景的唯一可靠解是云端 ASR 或 GPU + large-v3**。遗留：参考稿为 large-v3 生成（非人工），绝对 CER 待人工修订参考稿后成立 |
| 5 | B站搜索接口限流（HTTP 412）导致自动化找片不稳定 | 边界 | 与转写功能无关；测试视频已固定 |

## 七、验收命令复现

```bash
# 后端（终端 1，FFmpeg 已装则无需手动加 PATH；未装会自动降级 PyAV 解码）
cd "D:\Audio to text\backend"
.venv\Scripts\activate
uvicorn app.main:app --port 8000

# 前端（终端 2）
cd "D:\Audio to text"
# .env.local: NEXT_PUBLIC_USE_MOCK=false + BACKEND_ORIGIN=http://localhost:8000
pnpm dev

# 测试
cd backend && pytest            # 全量（含真实下载 + 推理）
pytest -m "not network"         # CI 同款
```
