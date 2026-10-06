# backend — FastAPI 视频解析与转写服务

已实现：
- `POST /api/parse`：yt-dlp 解析 B站视频链接 → `{ title, cover, duration, platform, videoId }`
- `POST /api/transcribe`：创建转写任务（BackgroundTasks 异步执行）→ `{ taskId, status, progress, stage }`；可选 `engine` 字段手动选择转写引擎（`local` 默认 / `cloud`，见下文云端 ASR 章节）
- `GET /api/transcribe/{taskId}`：轮询任务 → `{ status, stage, progress, transcript, plainText, video?, error? }`

转写流水线：优先解析 B站 CC 字幕（有则秒级返回）→ 否则 yt-dlp 下载音轨 → FFmpeg 转 16kHz 单声道 WAV →（可选）Demucs 人声分离 → faster-whisper 本地推理（或云端 API）。转写时把**热词 + 视频标题组合为 `initial_prompt`** 传入以引导专有名词识别；输出经轻量后处理（**zhconv 繁→简**、折叠 3 次以上的明显重复词、按语言补齐句末标点）。transcript 格式与前端契约一致：`[{ time, text }]`。

### 人声分离（可选，Demucs）

- 安装：`pip install -r requirements-separation.txt`（连带 torch，体积较大）；未安装时该步骤自动跳过
- 触发：`ENABLE_VOCAL_SEPARATION=true` 无条件启用；或 VAD 统计**非语音时长占比 > `VOCAL_SEP_TRIGGER_RATIO`（默认 0.4）** 时自动启用（BGM/音乐占比高的音频）
- 分离输出显式重采样为 16kHz 单声道 WAV，经 PyAV 解码为 float32 后喂给 ASR（与解码链格式契约一致，测试断言覆盖）
- **实测边界**：对「小模型幻觉型」强 BGM 场景（如快节奏带货解说）无效——幻觉与噪声无关；分离耗时约 0.6× 音频时长（150 秒音频约 91 秒 CPU）。数据见 [docs/asr-benchmark.md](../docs/asr-benchmark.md)

**中文准确率**：模型规格对 CER 的影响见 [docs/asr-benchmark.md](../docs/asr-benchmark.md)（base / small / medium / large-v3 实测对比表）。重建参考稿或新增评测视频：`python scripts/benchmark_asr.py --models base small --seconds 150`。

> 说明：B站 AI 字幕的播放器接口通常需登录 Cookie 才暴露，未登录时 yt-dlp 多数视频拿不到字幕，会自动走 ASR 路径（降级已实测验证）；配置 `BILI_COOKIE` 后可启用 AI 字幕快路径（见下文环境变量）。
> 任务注册表为**进程内存实现**（`services/tasks.py`，单进程 uvicorn 下验证正常）：任务状态只能通过 `create_task / get_task / update_task` 接口访问，路由与流水线不直接触碰存储——**多 worker / 云端部署时把该模块替换为 Redis 等实现即可，无需改业务代码**。

## 本地运行

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows（macOS/Linux: source .venv/bin/activate）
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8000
```

自检：<http://localhost:8000/api/health>；接口文档：<http://localhost:8000/docs>

> 转写可选依赖 **FFmpeg**（音轨转 16kHz 单声道 WAV）：未安装时自动降级为 PyAV 解码（faster-whisper 仍可运行）。安装：`winget install Gyan.FFmpeg`

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
| `ASR_ENGINE` | `local` | 默认引擎：`local` = faster-whisper 本地推理；`cloud` = OpenAI 兼容接口。另有本地引擎 `qwen3` / `funasr`（需 requirements-asr.txt，评测数据见 docs/asr-benchmark.md）。兼容旧变量名 `ASR_PROVIDER` |
| `ASR_ENGINE_FALLBACK` | `faster-whisper` | 主引擎**运行期**失败时的自动降级引擎（含云端网络/HTTP 故障）；留空禁用 |
| `WHISPER_MODEL` | `base` | 本地模型：`tiny` / `base` / `small` / `medium` / `large-v3`（越大越准越慢，首次自动下载）。**准确率建议：本地 CPU 用 `small` 或 `medium`；GPU 环境建议 `large-v3`**。CER 实测数据见 docs/asr-benchmark.md |
| `WHISPER_COMPUTE_TYPE` | `int8` | CPU 推荐 int8 |
| `CLOUD_ASR_BASE_URL` | `https://api.siliconflow.cn/v1` | 云端 ASR 服务地址（OpenAI 兼容），见下节 |
| `CLOUD_ASR_API_KEY` | 空 | 云端模式必填，见下节 |
| `CLOUD_ASR_MODEL` | `whisper-large-v3` | 云端模型名 |
| `BILI_COOKIE` | 空 | 可选。B站登录 Cookie：浏览器登录 bilibili.com → F12 → Application → Cookies 复制整段（含 SESSDATA）。配置后带 AI 字幕的视频可走字幕快路径秒级返回；未配置时自动降级「下载音频 + ASR」（已验证）。注意有效期与隐私，勿提交到 git |

> 旧变量名 `ASR_API_BASE` / `ASR_API_KEY` / `ASR_CLOUD_MODEL` 仍被兼容读取，新配置请用 `CLOUD_ASR_*`。

## 云端 ASR（手动选择，任务 D）

引擎切换**纯手动**：首页「转写模式」选择 `本地`（默认）/ `云端`，随 `POST /api/transcribe` 的
`engine` 字段提交（`local` / `cloud`，缺省跟随 `ASR_ENGINE` 环境变量）；
后端不做任何基于 VAD / 信噪比 / 时长的自动切换。

**默认指向硅基流动，使用云端必须配置 Key**（`backend/.env.local`）：

```bash
CLOUD_ASR_BASE_URL=https://api.siliconflow.cn/v1
CLOUD_ASR_API_KEY=sk-xxxx        # 必填，勿提交到 git
CLOUD_ASR_MODEL=whisper-large-v3
```

兼容的 OpenAI 协议服务商（按优先级）：

| 服务商 | `CLOUD_ASR_BASE_URL` | 模型示例 |
| --- | --- | --- |
| 硅基流动 SiliconFlow | `https://api.siliconflow.cn/v1` | `whisper-large-v3` |
| OpenRouter | `https://openrouter.ai/api/v1` | 按其模型目录 |
| OpenAI 直连 | `https://api.openai.com/v1` | `whisper-1` |

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
    ├── audio.py         # yt-dlp 下载音轨 + FFmpeg 转 16kHz 单声道 WAV
    ├── asr.py           # faster-whisper 本地推理 / 云端 OpenAI 兼容客户端
    ├── transcribe.py    # 流水线编排（BackgroundTasks 入口，进度更新）
    └── tasks.py         # 内存任务注册表
```

## 环境变量

见 `.env.example`。`CORS_ORIGINS` 默认放行本地前端；`CORS_ORIGIN_REGEX`
默认放行所有 `*.vercel.app`（预览与生产域）。
