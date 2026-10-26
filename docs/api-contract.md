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
| GET | `/api/capabilities` | 配置态探测 → `{ cloudAsrConfigured, summarizeConfigured }`（只返回布尔，**不含 Key 信息**） |
| GET | `/api/health` | 可达性探测 → `{ status: "ok" }` |

### 2.1 `POST /api/transcribe` 请求体

```jsonc
{
  "videoId": "BV1xx411c7mD",   // 必填
  "video": { /* VideoInfo，可选，透传解析结果 */ },
  "url": "https://www.bilibili.com/video/BV1xx411c7mD",  // 真实后端用
  "engine": "local"            // "local"（默认）| "cloud"
}
```

`engine` 是**纯手动**选择，随首页「转写模式」传入，后端不做自动判断。

### 2.2 `POST /api/summarize` 请求体

无状态接口，真实后端只读后四个字段（`taskId` 仅 Mock 使用）：

```jsonc
{
  "taskId": "abc123",
  "transcript": [{ "time": 12.5, "text": "..." }],
  "title": "视频标题",
  "duration": 1632,
  "detail": "standard" // 可选：brief | standard | detailed，缺省 standard
}
```

`detail` 控制总结的详略（对应后端 `DETAIL_PRESETS`：要点条数上限 3 / 5 / 7，概要字数与章节说明同步收敛）。
它是可选字段，老客户端不传也能正常调用（回落 `standard`），非法值由 pydantic 拒绝为 422。

`keyPoints[].time` 的时间戳强制取自原 `transcript`（后端做就近吸附校验），LLM 不能凭空编造。

## 3. 错误契约

所有非 2xx 响应统一为：

```json
{ "error": { "code": "INVALID_URL", "message": "链接格式不正确" } }
```

| 状态码 | code | 触发场景 |
| --- | --- | --- |
| 400 | `INVALID_URL` | 链接格式非法 |
| 400 | `CLOUD_NOT_CONFIGURED` | 选了云端引擎但未配 `CLOUD_ASR_API_KEY`（提交即报错，不静默降级） |
| 400 | `SUMMARIZE_NOT_CONFIGURED` | 未配 `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` |
| 404 | `INVALID_TASK` | taskId 不存在或已过期 |
| 409 | `TASK_INTERRUPTED` | 服务重启导致任务中断（前端给重试按钮） |
| 413 | `TRANSCRIPT_TOO_LONG` | 文字稿超过 10 万字，不支持总结 |
| 413 | `AUDIO_TOO_LARGE` | 音频超过 `MAX_AUDIO_SIZE_MB` |
| 422 | `UNSUPPORTED_PLATFORM` | 平台未接入（当前仅 B站） |
| 429 | `RATE_LIMITED` | 同一 IP 每分钟 POST 超过 `RATE_LIMIT_PER_MINUTE`（默认 10，0 表示禁用）。由 `RateLimitMiddleware` 直接返回，不经过业务层 |
| 500 | `TRANSCRIBE_FAILED` | 转写流程失败（含「未识别出语音内容」） |
| 500 | `TASK_TIMEOUT` | reaper 判定任务超时 |
| 500 | `INTERNAL_ERROR` | 未预期异常兜底 |
| 502 | `SUMMARIZE_FAILED` | LLM 调用失败（网络 / HTTP / 重试耗尽） |

状态码映射的代码事实来源是 [`src/lib/api-server.ts`](../src/lib/api-server.ts)（Mock 侧）与 [`backend/app/errors.py`](../backend/app/errors.py)（真实侧），两边必须一致。完整枚举定义见 `src/lib/types.ts` 的 `ApiErrorCode`——新增错误码时要同步改四处：`types.ts`、`api-server.ts` 的 `STATUS_BY_CODE`、`src/components/shared/error-alert.tsx` 的中文标题表（`Record<ClientErrorCode, string>` 会强制穷举）、本文档；漏改会类型报错。

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
