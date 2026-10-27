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
| POST | `/api/transcribe` | 创建转写任务 → `{ taskId, status, progress, stage }` |
| GET | `/api/transcribe/{taskId}` | 轮询任务 → `{ status, stage, progress, video?, transcript?, plainText?, error?, transcriptSource? }` |
| POST | `/api/summarize` | 生成总结（无状态）→ `{ summary, keyPoints, chapters }` |
| GET | `/api/capabilities` | 配置态与依赖探测 → `{ cloudAsrConfigured, summarizeConfigured, localAsrAvailable }`（只返回布尔，**不含 Key 信息**） |
| GET | `/api/health` | 可达性探测 → `{ status: "ok" }` |

### 2.1 `POST /api/transcribe` 请求体

```jsonc
{
  "videoId": "BV1xx411c7mD",   // 必填
  "video": { /* VideoInfo，可选，透传解析结果 */ },
  "url": "https://www.bilibili.com/video/BV1xx411c7mD",  // 真实后端用
  "engine": "local"            // 可选："local" | "cloud"；不传由后端按能力自动挑
}
```

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

状态码映射的代码事实来源是 [`src/lib/api-server.ts`](../src/lib/api-server.ts)（Mock 侧）与 [`backend/app/errors.py`](../backend/app/errors.py)（真实侧），两边必须一致。完整枚举定义见 `src/lib/types.ts` 的 `ApiErrorCode`——新增错误码时要同步改四处：`types.ts`、`api-server.ts` 的 `STATUS_BY_CODE`、`src/lib/error-titles.ts` 的中文标题表（`Record<ClientErrorCode, string>` 会强制穷举）、本文档；漏改会类型报错。

> `FILE_TOO_LARGE` / `FILE_FORMAT_UNSUPPORTED` 曾作为「本地视频上传」的预留码存在，但前后端都没有生产者，已于 P2 清理。M3 真正做上传时再一并引入（届时后端 `errors.py` 也要加对应异常类）。

## 4. Mock 实现说明

前端 Mock 走 Next Route Handlers（`src/app/api/*`），是**无状态**实现：

- `taskId` 自含上下文（base64url 编码），不依赖服务端存储
- 进度按时间推算，全程约 10 秒
- 演示失败路径：`https://www.bilibili.com/video/BV1FailDemo`（60% 处失败，用于验证重试）

> 桌面端构建时 `src/app/api/*` 会被临时移出（静态导出不支持 POST Route Handler），构建后还原。详见 [desktop-packaging.md](desktop-packaging.md)。

## 5. 路由约定（易踩坑）

任务页 URL **只能**通过 [`src/lib/routes.ts`](../src/lib/routes.ts) 的 `processingHref()` / `resultHref()` 生成：

| 形态 | 实际 URL |
| --- | --- |
| Web | `/processing/<taskId>` → 308 → `/processing?id=<taskId>` |
| 桌面端 | 直接 `/processing?id=<taskId>` |

组件里手拼 `` `/result/${id}` `` 在桌面端会 404。Web 端由 `next.config.mjs` 的 `redirects` 做 308 兼容，旧书签不会失效。
