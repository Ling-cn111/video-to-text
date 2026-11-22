# API 契约

> 前端与后端共享的类型定义在 [`src/lib/types.ts`](../src/lib/types.ts)，两边以它为唯一事实来源。
> 后端实现细节见 [backend/README.md](../backend/README.md)。

## 1. 地址映射

同一套前端代码，两种形态访问后端的路径不同，由 [`src/lib/api-base.ts`](../src/lib/api-base.ts) 的 `apiUrl()` 统一决定：

| 形态 | 实际请求 | 到后端变成 |
| --- | --- | --- |
| Web（Vercel / 本地） | `/backend-api/<path>` | Next rewrites 转发到 `${BACKEND_ORIGIN}/api/<path>` |
| 桌面端（静态导出） | `${NEXT_PUBLIC_API_BASE}/api/<path>` | 跨域直连后端 |

后端自身只认 `/api/*` 前缀。`/backend-api` 是前端同源代理的私有前缀，直连后端时**不要**带。

## 2. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/parse` | 解析视频链接 → `{ title, cover, duration, platform, videoId }` |
| POST | `/api/upload/init` | 签发本地上传凭证（M3）→ `{ uploadUrl, objectKey, expiresIn }`。**只有元数据，没有文件字节**——字节由浏览器直传 OSS |
| POST | `/api/transcribe` | 创建转写任务 → `{ taskId, status, progress, stage }`；`kind=file` 时走文件分支（见 §2.1） |
| GET | `/api/transcribe/{taskId}` | 轮询任务 → `{ status, stage, progress, video?, transcript?, plainText?, error?, transcriptSource? }` |
| POST | `/api/summarize` | 生成总结（无状态）→ `{ summary, keyPoints, chapters }` |
| POST | `/api/byok/test` | 测活（PR17）：极短真实请求验证「地址+模型+Key」→ `{ ok, latencyMs?, model? }` 或 `{ ok: false, code, message }`（HTTP 200，code 复用 ApiErrorCode）。Key 只走请求头 `X-Vtt-Llm-Key`，不落任何存储；校验类失败走标准错误形状 |
| POST | `/api/frames` | 重点帧抽帧（PR19）：按总结时间点生成视频画面截图 → `{ taskId, frames: { status, items? } }`。**异步**：受理后轮询 GET 任务详情读 `frames` 字段（见 §2.4） |
| GET | `/api/capabilities` | 配置态与依赖探测 → `{ cloudAsrConfigured, summarizeConfigured, localAsrAvailable, fileUploadConfigured, framesConfigured }`（只返回布尔，**不含 Key 信息**） |
| GET | `/api/health` | 可达性探测 → `{ status: "ok" }` |

### 2.0 `POST /api/upload/init`（M3 本地上传三步走）

上传通道是「客户端直传 OSS + 预签名 PUT」：FC HTTP 触发器同步请求体上限 32MB（平台硬限制），
文件字节**不可能**经过后端；后端只签发凭证与复核引用。三步：

```jsonc
// ① POST /api/upload/init（请求体只有元数据）
{ "fileName": "课堂录像.mp4", "size": 262144000, "mimeType": "video/mp4" }
// → { "uploadUrl": "https://<bucket>.oss-...aliyuncs.com/uploads/<uuid>/...?Signature=...",
//     "objectKey": "uploads/<uuid>/课堂录像.mp4", "expiresIn": 7200 }

// ② 浏览器直传：PUT {uploadUrl}，body = File，Content-Type 必须与签名一致（= mimeType）
//    XHR upload.onprogress 提供真实进度；可 abort 取消

// ③ POST /api/transcribe（见 §2.1 的 kind=file）
{ "kind": "file", "file": { "objectKey": "...", "fileName": "课堂录像.mp4", "size": 262144000 } }
```

约束：

- 扩展名白名单（mp4/mov/webm，`FILE_ALLOWED_EXTENSIONS`）与大小上限（`FILE_MAX_SIZE_MB`=500）在 ① 就被校验；
  ③ 时 HEAD 对象复核**实际大小**（声明可以撒谎）；Worker 下载时按磁盘与内存约束兜底。
- `uploadUrl` 只对 ① 生成的这一个 objectKey 有效、只允许 PUT、限时（`OSS_PRESIGN_EXPIRES_SECONDS`=7200）。
  上传超时重新走 ①，不要重试旧 PUT。
- objectKey 由后端生成（`uploads/{uuid}/{净化文件名}`）并按白名单复核：客户端拿不到「写任意位置」的能力。
- 未配置 OSS 时 ① 与 ③ 都返回 400 `FILE_STORAGE_NOT_CONFIGURED`；前端靠 `capabilities.fileUploadConfigured`
  提前隐藏入口。OSS 部署步骤见 [serverless-deploy.md](serverless-deploy.md)。

### 2.1 `POST /api/transcribe` 请求体

```jsonc
{
  "kind": "url",               // 可选："url"（默认，链接解析）| "file"（M3 本地上传）
  "videoId": "BV1xx411c7mD",   // kind=url 必填（缺失 422，与 M3 之前一致）；kind=file 可省
  "video": { /* VideoInfo，可选，透传解析结果 */ },
  "url": "https://www.bilibili.com/video/BV1xx411c7mD",  // kind=url 用，真实后端下载音频
  "file": { "objectKey": "uploads/...", "fileName": "课堂录像.mp4", "size": 262144000 },  // kind=file 必填
  "engine": "local",           // 可选："local" | "cloud"；不传由后端按能力自动挑
  "hotwords": ["Transformer", "字节跳动"]  // 可选：热词，引导 ASR 识别专有名词
}
```

`kind=file` 的行为（M3）：

- `file` 缺失 → 422（pydantic model_validator 按条件必填）；`objectKey` 不在白名单格式或对象不存在
  → 404 `FILE_NOT_FOUND`（预签名上传没完成就提交了）；实际大小超限 → 413 `FILE_TOO_LARGE`。
- 后端按引用合成 `VideoInfo`：`title=文件名、cover=''、duration=0、platform='local'、videoId=objectKey`
  （`video` 字段也可由调用方覆盖）。真实时长由 Worker 转码后按 WAV 字节数精确回填。
- 文件任务**没有链接语义**：跳过字幕快路径（初始 stage 直接 `extract_audio`），Worker 从 OSS
  内网 endpoint 拉对象 → ffmpeg 按路径转码 → 与 URL 任务在 ASR 汇合。
- **对象生命周期**：转写成功后立即删除；失败保留 ≤1 天（bucket 生命周期规则兜底），
  失败任务的「重试」带原 objectKey 重派，不需要重新上传。

`hotwords` 的行为：

- **进 `initial_prompt`**：后端把它们和视频标题拼成一句提示（`build_initial_prompt`），
  总长截断到 200 字符。人名、产品名、专业术语这类「听得出音但容易猜错字」的词收益最大。
- **只影响 ASR**：命中字幕快路径时用不到（字幕是 UP 主上传的文本，没有识别过程）。
  前端仍然照传——用户不知道这次会走哪条路径，行为不该随路径变化。
- **缺省 / 空数组等价**：不传就是不填，后端不会因此报错。超过 12 个或单条超过 20 字
  会在前端就被收敛掉（见 `src/lib/hotwords.ts`），不会出现「填了 50 个只生效 3 个」的困惑。

`engine` 的行为：

- **传了**：照它说的办（`cloud` 还要预检 Key，站点与请求头都没 Key 时 400 `CLOUD_NOT_CONFIGURED`）。
  只有 `capabilities.localAsrAvailable === true` 的自建部署才会传——那时首页显示选择器，用户的点击有意义。
- **不传**（网页端与桌面端的默认行为）：后端按可用性自动挑，顺序是
  「有云端 ASR Key → `cloud`（快、准）→ 否则本地引擎可用 → `local`（免费、慢）→ 都没有 → 400 `CLOUD_NOT_CONFIGURED`」。
  线上 FC 镜像按 `requirements-cloud.txt` 构建，**不含 faster-whisper**，所以自动挑的结果必然是 `cloud`。
  这条不能由前端猜：猜 `local` 会让线上任务在等满一分钟后以 `ModuleNotFoundError` 收场。

### 2.2 `POST /api/summarize` 请求体

无状态接口，真实后端读后五个字段（`taskId` 仅 Mock 使用）：

```jsonc
{
  "taskId": "abc123",
  "transcript": [{ "time": 12.5, "text": "..." }],
  "title": "视频标题",
  "duration": 1632,
  "detail": "standard", // 可选：brief | standard | detailed，缺省 standard
  "llmBaseUrl": "https://api.moonshot.cn/v1", // 可选：用户自填的 OpenAI 兼容接口地址
  "llmModel": "moonshot-v1-8k" // 可选：用户自填的模型名
}
```

`detail` 控制总结的详略（对应后端 `DETAIL_PRESETS`：要点条数上限 3 / 5 / 7，概要字数与章节说明同步收敛）。
它是可选字段，老客户端不传也能正常调用（回落 `standard`），非法值由 pydantic 拒绝为 422。

`llmBaseUrl` / `llmModel` 是「自定义服务商」的两半（第三半是请求头 `X-Vtt-Llm-Key`）：

- **缺省即回落**：不传就用站点配置的 `LLM_PRESETS[LLM_PROVIDER]` 的地址与模型，行为与旧版完全一致。
- **填了地址就必须填模型**：站点默认模型（如 `deepseek-flash`）在别家服务商那里不存在，发过去只会得到 `model not found`。该组合直接 400 `BYOK_ENDPOINT_INVALID`。
- **地址必须过 SSRF 校验**（`normalize_base_url`）：非 http(s)、环回 / 私有 / 链路本地 / 保留地址（含云厂商 metadata `169.254.169.254`）、`.local` / `.internal` 等内网域一律拒绝。这是放行用户输入的唯一安全前提。
- 地址末尾斜杠会被去掉（调用方统一 `f"{base_url}/chat/completions"`，双斜杠有的网关会 404）。
- **为什么地址与模型走请求体、Key 走请求头**：Key 是秘密，请求体会被错误日志与 `payload=` 打印碰到；地址与模型不是秘密，放请求体才能用 pydantic 校验并给出可读错误。

`keyPoints[].time` 的时间戳强制取自原 `transcript`（后端做就近吸附校验），LLM 不能凭空编造。

### 2.4 `POST /api/frames`（PR19 重点帧，URL 任务）

转写完成且 AI 总结就绪后，结果页可按总结的章节起点与要点时间生成视频画面截图。
**这是 completed 任务上的二次异步作业**：与转写共用同一个 Worker 函数（不同路由
`/internal/worker/frames`，事件体带 `kind: "frames"`），不占任务执行锁、不改任务
status，只写任务详情的 `frames` 字段。

```jsonc
// POST /api/frames
{ "taskId": "abc123", "timestamps": [0, 92, 196, 380, 560, 636] }
// → 受理或幂等返回现状：
{ "taskId": "abc123", "frames": { "status": "processing", "items": [] } }

// 轮询 GET /api/transcribe/{taskId}（frames 字段）：
{ "frames": {
    "status": "ready",
    "items": [
      { "time": 92, "objectKey": "frames/<taskId>/00.jpg", "url": "https://<bucket>.oss-...?Signature=...&Expires=..." }
    ]
  }
}
```

约束：

- **时间戳来源与净化**：`timestamps` 来自前端已拿到的 AI 总结（章节 `timeStart` + 要点 `time`，
  全是秒）；服务端不信任原始值——钳制到任务时长内（末尾留 1s）、按 2s 间隔去重、
  截断到 `FRAMES_MAX_COUNT`（默认 12）。请求体 `min_length=1`，缺省/空数组走 pydantic 422。
- **幂等**：`frames.status` 已是 processing / ready 时直接返回现状，不重复派发（重复点击
  与重试安全）；failed 允许重新派发。
- **范围（v1）**：仅 B站 URL 任务。file 任务 → 422 `FRAMES_UNSUPPORTED`（源对象成功即删，
  无源可抽）；未配 OSS → 400 `FILE_STORAGE_NOT_CONFIGURED`；任务未 completed → 409 `FRAMES_NOT_READY`。
- **产物**：每帧 1280 宽 JPEG（q:v 3，约 30-50KB），传 OSS `frames/{taskId}/{i}.jpg`，
  生命周期 7 天（与任务 TTL 对齐，见 setup_oss.py）。视频流经 playurl `try_look=1`
  取最高 1080P（优先 HEVC，下载代价最小），下载上限 `FRAMES_VIDEO_MAX_MB`。
- **`url` 是读取时现场签发的预签名 GET**（`FRAMES_PRESIGN_EXPIRES_SECONDS`=3600）：
  限时凭证不落任务库，过期后重拉任务详情即换新。前端 `<img>` 直连 OSS，无需 CORS。
- **失败收敛**：单帧失败跳过该时间点；整段失败（下载/全部帧失败）→ `frames.status=failed`
  + `frames.error={code:"FRAMES_FAILED", message}`——异步作业的失败不是 HTTP 错误，
  前端轮询任务详情即可见，重试按钮重新派发。
- capabilities 新增 `framesConfigured`（与 `fileUploadConfigured` 同源 = OSS 四项齐备），
  前端据此隐藏入口。

### 2.3 用户自带 Key（BYOK）请求头

两个可选请求头，用于让用户用自己的额度而不是站点额度：

| 请求头 | 作用 | 缺失时 |
| --- | --- | --- |
| `X-Vtt-Llm-Key` | `POST /api/summarize` 的 LLM Key | 回落站点 `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` |
| `X-Vtt-Asr-Key` | `POST /api/transcribe`（`engine=cloud`）的云端 ASR Key | 回落站点 `CLOUD_ASR_API_KEY` |

**界面只收集 `X-Vtt-Llm-Key`**：云端 ASR 用的是硅基流动的免费模型（刊例价 0 元/千字符），
用户没有自备 Key 的必要，多一个输入框只是多一道门槛。`X-Vtt-Asr-Key` 保留给脚本与
API 调用方，前端不再发送。

约定：

- **只走请求头，不进请求体**。请求体会被错误日志、`payload=` 打印、体积限制一整套链路碰到；请求头是唯一不会被顺手打进日志的位置。
- 值超过 512 字符直接丢弃并记 warning（`normalize_key`），**不做 `sk-` 前缀校验**——各家格式不统一，猜错会拒掉合法 Key。
- **服务地址与模型由请求体的 `llmBaseUrl` / `llmModel` 指定，且必须过 SSRF 校验**（见 §2.2）。旧版把地址钉死在站点配置里，导致用户填了别家 Key 必然 401；现在放行输入，但只放行公网 http(s) 地址。**云端 ASR 的地址仍不接受用户输入**。
- **填了 `llmBaseUrl` 就必须同时带 `X-Vtt-Llm-Key`**，否则 400 `BYOK_ENDPOINT_INVALID`。理由：Key 的回落目标是站点自己的 Key，若放行「地址是我的、Key 用站点的」，站点凭据会被发往用户指定的服务器。这条与「填了地址必须填模型」是同一条规则的三个部分。
- **SSRF 校验包含 DNS 解析后复检**：只查字面量挡不住 `127.0.0.1.nip.io` / `localtest.me` 这类把 IP 编进域名的公共 DNS 服务。解析失败一律拒绝。
- 站点未配 Key 但请求带了 BYOK Key 时**必须放行**（这正是该功能的意义）。
- Key 不落任务库：`TranscribeJob.byok_key` 只随作业跨进程投递，`tasks._payload_dict` 里不含该字段。
- 前后端常量必须逐字一致：`src/lib/byok.ts` 的 `HEADER_LLM_KEY` / `HEADER_ASR_KEY` ↔ `backend/app/services/byok.py` 的同名常量。名字不一致**不会报错**，只会静默回落站点 Key。

## 3. 错误契约

所有非 2xx 响应统一为：

```json
{ "error": { "code": "INVALID_URL", "message": "链接格式不正确" } }
```

| 状态码 | code | 触发场景 |
| --- | --- | --- |
| 400 | `INVALID_URL` | 链接格式非法 |
| 400 | `CLOUD_NOT_CONFIGURED` | 选了云端引擎但站点未配 `CLOUD_ASR_API_KEY` 且请求未带 `X-Vtt-Asr-Key`（提交即报错，不静默降级） |
| 400 | `SUMMARIZE_NOT_CONFIGURED` | 站点未配 `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY`，且请求未带 `X-Vtt-Llm-Key` |
| 401 | `BYOK_KEY_INVALID` | 请求头里的用户 Key 被服务商拒绝（401/403）。**动作主体是用户自己**：文案指向「API 设置」，不要提示去找部署者 |
| 404 | `INVALID_TASK` | taskId 不存在或已过期 |
| 409 | `TASK_INTERRUPTED` | 服务重启导致任务中断（前端给重试按钮） |
| 413 | `TRANSCRIPT_TOO_LONG` | 文字稿超过 10 万字，不支持总结 |
| 413 | `AUDIO_TOO_LARGE` | 音频超过 `MAX_AUDIO_SIZE_MB` |
| 422 | `UNSUPPORTED_PLATFORM` | 平台未接入（当前仅 B站） |
| 429 | `RATE_LIMITED` | 触发限流：单个客户端每分钟 POST 超过 `RATE_LIMIT_PER_MINUTE`（默认 10），或全站合计超过 `RATE_LIMIT_GLOBAL_PER_MINUTE`（默认 120），0 表示禁用。响应带 `Retry-After` 秒数。由 `RateLimitMiddleware` 直接返回，不经过业务层 |
| 500 | `TRANSCRIBE_FAILED` | 转写流程失败（含「未识别出语音内容」） |
| 500 | `TASK_TIMEOUT` | reaper 判定任务超时 |
| 500 | `INTERNAL_ERROR` | 未预期异常兜底 |
| 502 | `SUMMARIZE_FAILED` | LLM 调用失败（网络 / HTTP / 重试耗尽） |
| 413 | `FILE_TOO_LARGE` | 本地上传源文件超 `FILE_MAX_SIZE_MB`（前端预校验与后端共用此码，M3） |
| 422 | `FILE_FORMAT_UNSUPPORTED` | 扩展名白名单外（mp4/mov/webm 之外，M3） |
| 400 | `FILE_STORAGE_NOT_CONFIGURED` | 站点未配 OSS（`OSS_BUCKET` 等），本地上传整体不可用（M3） |
| 404 | `FILE_NOT_FOUND` | 引用的 OSS 对象不存在：预签名上传未完成就发起转写 / 对象已被清理（M3） |
| 422 | `FRAMES_UNSUPPORTED` | 重点帧目标不支持：文件来源任务 / 非 B站链接（PR19；前端按能力与任务来源预先隐藏入口，此码兜住直调 API） |
| 409 | `FRAMES_NOT_READY` | 任务尚未转写完成，不能抽帧（PR19） |

`FRAMES_FAILED`（PR19）不是 HTTP 错误码：它只出现在任务详情 `frames.error.code` 里
（Worker 异步作业的失败收敛进任务详情），前端按 `frames.status=failed` 展示。

状态码映射的代码事实来源是 [`src/lib/api-server.ts`](../src/lib/api-server.ts)（Mock 侧）与 [`backend/app/errors.py`](../backend/app/errors.py)（真实侧），两边必须一致。完整枚举定义见 `src/lib/types.ts` 的 `ApiErrorCode`——新增错误码时要同步改四处：`types.ts`、`api-server.ts` 的 `STATUS_BY_CODE`、`src/lib/error-titles.ts` 的中文标题表（`Record<ClientErrorCode, string>` 会强制穷举）、本文档；漏改会类型报错。

> M3 已引入 `FILE_TOO_LARGE` / `FILE_FORMAT_UNSUPPORTED`（此前作为预留码在 P2 清理过，本次随本地上传功能带上了真实生产者，另有 `FILE_STORAGE_NOT_CONFIGURED` / `FILE_NOT_FOUND` 两个配套码）。

## 4. Mock 实现说明

前端 Mock 走 Next Route Handlers（`src/app/api/*`），是**无状态**实现：

- `taskId` 自含上下文（base64url 编码），不依赖服务端存储
- 进度按时间推算，全程约 10 秒
- 演示失败路径：`https://www.bilibili.com/video/BV1FailDemo`（60% 处失败，用于验证重试）
- 重点帧（PR19）：ready 随 POST /api/frames 响应立即返回（无状态，不模拟 processing 时序——
  该阶段由 E2E 路由拦截覆盖）；编造 taskId 带 `fr: 1` 注入 frames failed（回归 40c）；
  capabilities 恒 `framesConfigured: true`；帧图占位复用平台封面 SVG

> 桌面端构建时 `src/app/api/*` 会被临时移出（静态导出不支持 POST Route Handler），构建后还原。详见 [desktop-packaging.md](desktop-packaging.md)。

## 5. 路由约定（易踩坑）

任务页 URL **只能**通过 [`src/lib/routes.ts`](../src/lib/routes.ts) 的 `processingHref()` / `resultHref()` 生成：

| 形态 | 实际 URL |
| --- | --- |
| Web | `/processing/<taskId>` → 308 → `/processing?id=<taskId>` |
| 桌面端 | 直接 `/processing?id=<taskId>` |

组件里手拼 `` `/result/${id}` `` 在桌面端会 404。Web 端由 `next.config.mjs` 的 `redirects` 做 308 兼容，旧书签不会失效。
