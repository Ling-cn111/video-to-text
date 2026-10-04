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
| 2 | B站 AI 字幕需 `BILI_COOKIE`，未实现读取逻辑（仅文档预留） | 边界 | 后续实现 yt-dlp cookie 透传后端到端补验字幕快路径 |
| 3 | 任务注册表为进程内存：重启后任务丢失（前端刷新会提示任务不存在并可重试） | 边界 | 多 worker 部署前换 Redis |
| 4 | whisper base 模型对专业词汇/英文歌词有识别误差 | 质量 | `WHISPER_MODEL=small` 可提升；后续支持热词 |
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
