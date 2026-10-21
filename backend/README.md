# backend — FastAPI 视频解析与转写服务

已实现：
- `POST /api/parse`：yt-dlp 解析 B站视频链接 → `{ title, cover, duration, platform, videoId }`
- `POST /api/transcribe`：创建转写任务（默认 `ENQUEUE_MODE=inline` 走 BackgroundTasks 异步执行）→ `{ taskId, status, progress, stage }`；可选 `engine` 字段手动选择转写引擎（`local` 默认 / `cloud`，见下文云端 ASR 章节）
- `GET /api/transcribe/{taskId}`：轮询任务 → `{ status, stage, progress, transcript, plainText, video?, error?, transcriptSource? }`
- `POST /api/summarize`：AI 总结（无状态）→ 请求 `{ transcript, title, duration }`，响应 `{ summary, keyPoints: [{time,text}], chapters: [{title,timeStart,timeEnd,note}] }`；keyPoints 时间戳强制取自原 transcript（后端就近吸附校验）

转写流水线：**分层字幕快路径**（yt-dlp CC 字幕 → B站弹幕元数据接口 AI 字幕，命中即秒级返回）→ 否则 yt-dlp 下载音轨 → FFmpeg 转 16kHz 单声道 WAV →（可选）Demucs 人声分离 → faster-whisper 本地推理（或云端 API）。转写时把**热词 + 视频标题组合为 `initial_prompt`** 传入以引导专有名词识别；输出经轻量后处理（**zhconv 繁→简**、折叠 3 次以上的明显重复词、按语言补齐句末标点）。transcript 格式与前端契约一致：`[{ time, text }]`，并带 `transcriptSource` 来源标记（subtitle_cc / subtitle_ai / asr，前端据此显示「来源：B站字幕」Badge）。

> **音频去磁盘化（S1-B）**：下载 / FFmpeg 转换 / ASR 全程以内存 `BytesIO` 流转，不写工作目录（`AUDIO_DIR` 已移除）。yt-dlp 取直链后经 httpx 流式拉入内存（拿不到直链时回退系统临时目录，读完即删）；FFmpeg 走管道（`pipe:0` → `pipe:1`）；云端 ASR 直接以内存 bytes 上传。仅 demucs / qwen3 / funasr 等「只认文件路径」的组件落盘到系统临时目录并**即用即删**。音频大小上限 `MAX_AUDIO_SIZE_MB`（默认 100），超限抛 `AUDIO_TOO_LARGE`（「音频过大，暂不支持」），避免整块载入 OOM。

### 字幕快路径（分层降级，任务 G）

有字幕的视频完全不需要 ASR：直接读字幕，秒级返回、零成本、质量优于任何 ASR。

| 层级 | 来源 | 认证 | 说明 |
| --- | --- | --- | --- |
| 1 | yt-dlp CC 字幕（UP 主手传） | 无 | 未登录即可，覆盖率低但质量最高 |
| 2 | **弹幕元数据接口 `/x/v2/dm/view?aid=&oid=&type=1`** 的 AI 字幕 | **无** | 未登录零 Cookie 零签名可取（`ai-zh` 中文自动生成 + 多语种翻译）；`ai_status=2`（已生成）才收录。覆盖实测 6 视频中 5 个，侦察报告见 [docs/bili-subtitle-recon.md](docs/bili-subtitle-recon.md) |
| 3 | （预留）yt-dlp + `BILI_COOKIE` | Cookie | 兜底层，未实现；dm/view 覆盖不到位时再启用 |
| 4 | ASR（本地 small / 云端） | — | 全部无字幕时的兜底（见 docs/asr-benchmark.md） |

每层失败静默降级不中断；命中层写入 `transcriptSource`。注意 B站 AI 字幕**非全覆盖**（如央视新闻部分视频为 0 轨道），层 4 兜底必要。

### 人声分离（可选，Demucs）

- 安装：`pip install -r requirements-separation.txt`（连带 torch，体积较大）；未安装时该步骤自动跳过
- 触发：`ENABLE_VOCAL_SEPARATION=true` 无条件启用；或 VAD 统计**非语音时长占比 > `VOCAL_SEP_TRIGGER_RATIO`（默认 0.4）** 时自动启用（BGM/音乐占比高的音频）
- 分离输出显式重采样为 16kHz 单声道 WAV（内存 `BytesIO` 进/出，S1-B），经 PyAV 解码为 float32 后喂给 ASR（与解码链格式契约一致，测试断言覆盖）；demucs 运行所需文件在系统临时目录即用即删
- **实测边界**：对「小模型幻觉型」强 BGM 场景（如快节奏带货解说）无效——幻觉与噪声无关；分离耗时约 0.6× 音频时长（150 秒音频约 91 秒 CPU）。数据见 [docs/asr-benchmark.md](../docs/asr-benchmark.md)

**中文准确率**：模型规格对 CER 的影响见 [docs/asr-benchmark.md](../docs/asr-benchmark.md)（base / small / medium / large-v3 实测对比表）。重建参考稿或新增评测视频：`python scripts/benchmark_asr.py --models base small --seconds 150`。

> 说明：B站 AI 字幕通过**弹幕元数据接口 `/x/v2/dm/view`** 未登录即可获取（任务 G，无需 Cookie）；
> yt-dlp 层未登录仅能拿到 UP 主手传 CC 字幕（AI 字幕轨道为空，实测）。`BILI_COOKIE` 保留为
> 层 3 预留兜底（未实现）：浏览器登录 bilibili.com → F12 → Application/存储 → Cookies →
> 复制整段（含 SESSDATA）。任务状态为 **TaskStore 抽象存储**（任务 H + S1-A，`services/tasks.py`）：默认 `MemoryTaskStore`——SQLite 持久化（`backend/data/tasks.db`，`TASK_DB_PATH` 可配）+ 内存一级缓存（进程内轮询零 SQL），写操作同步落盘；**服务重启后历史任务可查，重启时未完成任务自动标记 `interrupted`**，前端显示「任务已中断，请重试」+ 重试按钮。任务状态只能通过 `create_task / get_task / update_task` 接口访问，路由与流水线不直接触碰存储。`TASK_STORE=redis` + `REDIS_URL`（可选依赖 `requirements-redis.txt`）切换到 `RedisTaskStore`（redis.asyncio），行为与 memory 对齐（`tests/test_tasks_store.py` 双实现同用例验证）；**REDIS_URL 未配置时自动降级 memory 并打警告**。多 worker / 云端部署用 redis 即可，无需改业务代码（SQLite 为单机文件库，`backend/data/` 已入 .gitignore）。

> **任务生命周期（S2-P0）**：① `TASK_TTL_SECONDS`（默认 7 天，0 = 永久）——Redis 写入统一带 `EX` 并滑动续期，任务 JSON 含完整文字稿，无 TTL 会无限增长；② `TASK_INIT_MODE=recover|none`——启动恢复只在**陈旧**（`updated_at` 超过 `TASK_STALE_SECONDS`，默认 600s）时才把 `processing` 标记 `interrupted`，**Worker / 只读实例必须设 `none`**（Serverless 每次冷启动都会跑启动初始化，无脑全量改写会误杀其他实例在跑的任务）；③ 进度上报节流（进度步长 5）+ **心跳线程**（`TASK_HEARTBEAT_SECONDS`，默认 30s）——下载 / FFmpeg / ASR 等长阻塞步骤期间持续刷新 `updated_at`，供陈旧判定与后续 reaper 使用。

## 本地运行

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows（macOS/Linux: source .venv/bin/activate）
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8000
```

自检：<http://localhost:8000/api/health>；接口文档：<http://localhost:8000/docs>

> 转写可选依赖 **FFmpeg**（音轨经管道转 16kHz 单声道 WAV）：未安装时自动降级为 PyAV 解码（faster-whisper 仍可运行）。安装：`winget install Gyan.FFmpeg`

## 容器化部署（S1-D）

`backend/Dockerfile` 构建**云端 ASR 专用**镜像（Serverless / 阿里云 FC 等）：只装
`requirements-cloud.txt`（含 `redis`），**不含** faster-whisper / torch / demucs / qwen-asr / funasr
等本地重型依赖；FFmpeg 用静态单文件（约 78MB）替代 apt 完整包。构建上下文为 `backend/`
（`backend/.dockerignore` 已排除 `.env.local` / `tests/` / `data/` 等）。

```bash
cd backend
docker build -t video-to-text-backend:local .    # 实测镜像约 406MB（目标 < 1GB）

docker run --rm -p 8000:8000 \
  -e ASR_ENGINE=cloud \
  -e CLOUD_ASR_API_KEY=sk-xxxx \
  -e ASR_ENGINE_FALLBACK= \
  -e TASK_STORE=redis \
  -e REDIS_URL=redis://host:6379/0 \
  -e CORS_ORIGINS=https://your-frontend.example \
  video-to-text-backend:local

curl -s localhost:8000/api/health    # {"status":"ok"}
```

部署要点：

- **端口**：阿里云 FC 的「监听端口」需设为 `8000`（与 `EXPOSE` / `CMD` 一致）。
- **配置全部经环境变量注入**，Dockerfile 不硬编码任何业务配置：`ASR_ENGINE` / `ASR_ENGINE_FALLBACK` /
  `CLOUD_ASR_*` / `LLM_*` / `TASK_STORE` / `REDIS_URL` / `RATE_LIMIT_PER_MINUTE` / `CORS_ORIGINS`。
- **多实例必须** `TASK_STORE=redis` + `REDIS_URL`（否则任务存储与限流会降级为进程内，跨实例不一致）。
- 容器内无 faster-whisper → 建议 `ASR_ENGINE_FALLBACK=`（空，禁用降级）；否则云端失败后的降级会报
  「本地语音识别组件未安装」（优雅失败，但无意义）。
- 容器只需 `/tmp` 可写（S1-B 的 yt-dlp 临时目录与 `_materialized_audio` 桥接用）。
- 自检：`docker exec <container> which ffmpeg` → `/usr/local/bin/ffmpeg`；`which yt-dlp` → `/opt/venv/bin/yt-dlp`。
- **受限网络**：若无法直连 Docker Hub（大陆常见），先在 Docker Desktop → Settings → Docker Engine 配置 `registry-mirrors` 再构建。

## 前端联调

```bash
# 仓库根目录
cp .env.example .env.local      # 修改 NEXT_PUBLIC_USE_MOCK=false（BACKEND_ORIGIN 默认即本地 8000）
pnpm dev                        # 等价于 npm run dev
```

粘贴 B站链接，前端进度页/结果页即显示真实标题、封面、时长与真实转写文字稿
（AI 总结当前仍为内置 Mock）。

前端请求走同源 `/backend-api/*`，由 Next.js rewrites 服务端代理到 `BACKEND_ORIGIN`，
浏览器不直接跨域访问后端；后端 CORS（本地 + Vercel 域）仍按契约保留。

## 转写配置（环境变量）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ASR_ENGINE` | `cloud` | 默认引擎（S1-C）：`cloud` = OpenAI 兼容接口（**需配 `CLOUD_ASR_API_KEY`**，未配时提交报 `CLOUD_NOT_CONFIGURED`）；`local` = faster-whisper 本地推理。**本地开发 / 离线请在 `.env.local` 设 `ASR_ENGINE=local`**。另有本地引擎 `qwen3` / `funasr`（需 requirements-asr.txt，评测数据见 docs/asr-benchmark.md）。兼容旧变量名 `ASR_PROVIDER` |
| `ASR_ENGINE_FALLBACK` | `local` | 主引擎**运行期**失败时的自动降级引擎（含云端网络/HTTP 故障，默认回退本地 faster-whisper）；留空禁用 |
| `WHISPER_MODEL` | `base` | 本地模型：`tiny` / `base` / `small` / `medium` / `large-v3`（越大越准越慢，首次自动下载）。**准确率建议：本地 CPU 用 `small` 或 `medium`；GPU 环境建议 `large-v3`**。CER 实测数据见 docs/asr-benchmark.md |
| `WHISPER_COMPUTE_TYPE` | `int8` | CPU 推荐 int8 |
| `MAX_AUDIO_SIZE_MB` | `100` | 音频大小上限（MB，S1-B 内存化）。下载时按 content-length 预检 + 边拉边累计，超限抛 `AUDIO_TOO_LARGE`（「音频过大，暂不支持」），避免整块载入 OOM |
| `CLOUD_ASR_BASE_URL` | `https://api.siliconflow.cn/v1` | 云端 ASR 服务地址（OpenAI 兼容），见下节 |
| `CLOUD_ASR_API_KEY` | 空 | 云端模式必填，见下节 |
| `CLOUD_ASR_MODEL` | `XingChenAGI/XingChenASR-V3.2-Ultra` | 云端模型名 |
| `BILI_COOKIE` | 空 | **预留兜底层，未实现**（AI 字幕已由 dm/view 接口未登录覆盖，见上「字幕快路径」）。启用后语义：yt-dlp 携带 Cookie 拿 AI 字幕。获取方式：浏览器登录 bilibili.com → F12 → Application → Cookies 复制整段（含 SESSDATA）。注意有效期与隐私，勿提交到 git |

> 旧变量名 `ASR_API_BASE` / `ASR_API_KEY` / `ASR_CLOUD_MODEL` 仍被兼容读取，新配置请用 `CLOUD_ASR_*`。

## AI 总结（LLM，阶段三 / 任务 E）

`POST /api/summarize` 无状态接收 `{transcript, title, duration}`，经 LLM 生成结构化总结。配置（`backend/.env.local`）：

```bash
LLM_PROVIDER=deepseek            # deepseek | qwen（通义千问兼容模式）
DEEPSEEK_API_KEY=sk-xxxx         # 二选一；qwen 用 DASHSCOPE_API_KEY
# LLM_MODEL=deepseek-flash       # 可选覆盖默认模型（qwen 默认 qwen-plus）
```

- 未配置 Key 时请求即报 400 `SUMMARIZE_NOT_CONFIGURED`（不静默降级）；文字稿超 10 万字报 413 `TRANSCRIPT_TOO_LONG`
- 结构化输出 `response_format=json_object` + 解析失败自动重试（最多 2 次）
- **keyPoints 时间戳三重保障**：输入文字稿格式化为 `[mm:ss] text` → prompt 明确禁止编造 → 后端对输出 time 就近吸附到真实时间戳
- 长文 chunking：超 6000 字按条目边界切块（条目不拆），逐块提取要点 → 合并终稿；时间戳全程指向原 transcript
- 成本：每次调用记录 usage tokens 与估算成本（`LLM_PRICE_TABLE`，占位单价待官网核对）→ 日志；实测数据见 [docs/llm-cost.md](../docs/llm-cost.md)

## 请求限流（任务 H + S1-C）

令牌桶，**仅拦截 POST**（GET 轮询不限）：每 IP 每分钟 `RATE_LIMIT_PER_MINUTE` 次（默认 10，**0 = 禁用**）。IP 取 `X-Forwarded-For` 首段（同源代理场景），缺省用直连地址。超限返回 429：

```json
{ "error": { "code": "RATE_LIMITED", "message": "请求太频繁，请稍后再试" } }
```

存储（S1-C）：**配置 `REDIS_URL` 后使用 Redis 共享计数**（多后端实例总配额一致；`WATCH/MULTI/EXEC` 乐观事务保证读改写原子，key `ratelimit:<ip>`，TTL 120s）。未配置 `REDIS_URL`、redis 包缺失或**运行期 Redis 故障**时自动降级进程内令牌桶，服务不中断；降级警告 60 秒内同类错误只记一次，避免 Redis 宕机时日志被刷爆。多实例部署需 `pip install -r requirements-redis.txt`。

实现：`app/services/rate_limit.py`（`MemoryRateLimiter` / `RedisRateLimiter` + 纯 ASR 中间件）。

## 云端 ASR（手动选择，任务 D）

引擎切换**纯手动**：首页「转写模式」选择 `本地`（默认）/ `云端`，随 `POST /api/transcribe` 的
`engine` 字段提交（`local` / `cloud`，缺省跟随 `ASR_ENGINE` 环境变量）；
后端不做任何基于 VAD / 信噪比 / 时长的自动切换。

**默认指向硅基流动，使用云端必须配置 Key**（`backend/.env.local`）：

```bash
CLOUD_ASR_BASE_URL=https://api.siliconflow.cn/v1
CLOUD_ASR_API_KEY=sk-xxxx        # 必填，勿提交到 git
CLOUD_ASR_MODEL=XingChenAGI/XingChenASR-V3.2-Ultra
```

兼容的 OpenAI 协议服务商（按优先级）：

| 服务商 | `CLOUD_ASR_BASE_URL` | 模型示例 |
| --- | --- | --- |
| 硅基流动 SiliconFlow | `https://api.siliconflow.cn/v1` | `XingChenAGI/XingChenASR-V3.2-Ultra`（默认，带分段时间戳）、`Qwen/Qwen3-ASR-1.7B`、`FunAudioLLM/SenseVoiceSmall`（两者仅整段 text，无时间戳） |
| OpenRouter | `https://openrouter.ai/api/v1` | 按其模型目录 |
| OpenAI 直连 | `https://api.openai.com/v1` | `whisper-1` |

> 注：硅基流动已不再提供 whisper-large-v3（2026-10 实测）；引擎首选 `response_format=verbose_json`
> 取分段时间戳，服务商不支持（HTTP 400）时自动降级 `json`——输出退化为整段单条，
> 「时间戳定位」视图在该模型下退化为单行（全文阅读不受影响）。

行为约定：

- **未配置 `CLOUD_ASR_API_KEY` 时，前端选择云端在提交即报 400 `CLOUD_NOT_CONFIGURED`**
  （明确中文提示「请先在后端设置 CLOUD_ASR_API_KEY…或使用本地模式」），不会静默创建任务、也不会静默降级。
- 云端**运行期**故障（网络 / HTTP 错误）按 `ASR_ENGINE_FALLBACK`（默认 `faster-whisper`）自动降级本地，任务不失败。
- 服务地址在每次请求前经 url_guard 校验：仅允许公网 http/https（拒绝 localhost / 环回 / 私有 / 保留地址）。
- **隐私提示**：云端模式会把下载的音频上传至所配置的第三方服务商处理，前端首页选择云端时已明确提示用户。
- 通义听悟 / 火山引擎等非 OpenAI 协议暂不支持：按 `services/asr.py` 的 `BaseASREngine` 接口新增引擎类即可（预留扩展点）。

## 测试

```bash
pytest                    # 全量（含真实 B站下载 + whisper 推理的集成测试，首次较慢）
pytest -m "not network"   # 跳过外网/重推理集成测试（CI 使用）
pytest -m network         # 只跑真实集成测试
```

## 结构

```
backend/app/
├── main.py          # FastAPI 入口：CORS、统一错误形状 { error: { code, message } }
├── config.py        # CORS / ASR 配置（env 可覆盖）
├── schemas.py       # Pydantic 契约（与 src/lib/types.ts 一致）
├── platforms.py     # 平台注册表（与前端 registry 对应；当前仅 bilibili）
├── url_guard.py     # URL 安全守卫：仅 http/https，拒绝 localhost/私有/保留地址
├── errors.py        # AppException（INVALID_URL 400 / UNSUPPORTED_PLATFORM 422 / INVALID_TASK 404 / …）
├── routers/
│   ├── parse.py     # POST /api/parse
│   └── transcribe.py    # POST /api/transcribe + GET /api/transcribe/{taskId}
└── services/
    ├── parser.py        # yt-dlp 元数据解析
    ├── subtitles.py     # CC/AI 字幕轨道选择与解析（bilibili json / json3 / vtt）
    ├── audio.py         # yt-dlp 下载音轨到内存（BytesIO）+ FFmpeg 管道转 16kHz 单声道 WAV
    ├── asr.py           # faster-whisper 本地推理 / 云端 OpenAI 兼容客户端
    ├── transcribe.py    # 流水线编排（进度更新 + 心跳）
    ├── dispatcher.py    # 任务分发（ENQUEUE_MODE：inline | fc | mns | redis）
    └── tasks.py         # 任务注册表（SQLite 持久化 backend/data/tasks.db + 内存写穿缓存）
```

## 环境变量

见 `.env.example`。**启动时自动加载 `backend/.env.local`**（`config.py` 顶部逐行解析，KEY=VALUE；
显式导出的环境变量优先于文件值）——run.bat / 控制台.ps1 / 手动 uvicorn 等所有启动方式统一生效，
改完 Key 后重启后端即可（`run.bat restart` 或托盘「重启」）。
`CORS_ORIGINS` 默认放行本地前端；`CORS_ORIGIN_REGEX` 默认放行所有 `*.vercel.app`（预览与生产域）。

**任务分发（S2-P1）**：`POST /api/transcribe` 不再直接 `background_tasks.add_task(...)`，而是构造
`TranscribeJob`（`task_id` / `url` / `video` / `prefer_subtitles` / `hotwords` / `engine` 六元组，可 `to_dict()` 序列化）
交给 `services/dispatcher.enqueue()`。`ENQUEUE_MODE=inline`（默认）时行为与改造前**完全一致**；
`fc` / `mns` / `redis` 是 P3/P4 的实现位，当前显式抛 `DispatcherNotImplementedError`（绝不静默丢任务），
路由会把该任务标记为 `failed` 再返回 500，避免留下永远 `processing` 的孤儿任务。配错模式值会告警并降级 `inline`。
