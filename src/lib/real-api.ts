import { apiUrl } from '@/lib/api-base'
import { byokBodyFor, byokHeaderFor } from '@/lib/byok'
import { JSON_HEADERS, toJson, throwNetworkError, type ApiClient } from '@/lib/http'
import type {
  Capabilities,
  GetTaskResponse,
  HealthResponse,
  StartTaskResponse,
  SummarizeRequest,
  Summary,
  TranscribeEngine,
  VideoInfo,
} from '@/lib/types'

/**
 * 真实后端客户端（FastAPI + yt-dlp + faster-whisper + LLM 总结，代码见 backend/）。
 *
 * 解析 / 转写 / AI 总结均走真实后端。地址由 lib/api-base.ts 的 apiUrl() 决定：
 * - web：同源 /backend-api/*，由 Next rewrites 服务端代理到 BACKEND_ORIGIN
 * - 桌面端：NEXT_PUBLIC_API_BASE + /api/*，跨域直连后端
 * 切换：NEXT_PUBLIC_USE_MOCK=false 时由 lib/api.ts 选用本客户端。
 */

export const realApi: ApiClient = {
  /** POST /parse（web: /backend-api/parse → 后端 /api/parse） */
  parseVideo: (url: string): Promise<VideoInfo> =>
    toJson(
      fetch(apiUrl('/parse'), { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ url }) }).catch(
        throwNetworkError,
      ),
    ),

  startTranscription: (
    videoId: string,
    video?: VideoInfo,
    url?: string,
    engine?: TranscribeEngine,
  ): Promise<StartTaskResponse> =>
    toJson(
      fetch(apiUrl('/transcribe'), {
        method: 'POST',
        // 云端 ASR 用的是站点的免费模型 Key，界面不再收集用户自带的 ASR Key
        headers: JSON_HEADERS,
        body: JSON.stringify({ videoId, video, url, engine }),
      }).catch(throwNetworkError),
    ),

  getTask: (taskId: string): Promise<GetTaskResponse> =>
    toJson(fetch(apiUrl(`/transcribe/${encodeURIComponent(taskId)}`)).catch(throwNetworkError)),

  /** POST /summarize：真实 LLM 总结（无状态：直接传文字稿与元数据，taskId 仅 Mock 用） */
  summarize: (request: SummarizeRequest): Promise<Summary> =>
    toJson(
      fetch(apiUrl('/summarize'), {
        method: 'POST',
        // 用户自带 LLM Key 时随请求带上（没填则空对象，后端回落站点 Key）
        headers: { ...JSON_HEADERS, ...byokHeaderFor() },
        body: JSON.stringify({
          transcript: request.transcript,
          title: request.title,
          duration: request.duration,
          detail: request.detail ?? 'standard',
          // 接口地址与模型走请求体：不是秘密，且需要后端校验后给出可读错误
          ...byokBodyFor(),
        }),
      }).catch(throwNetworkError),
    ),
  /** GET /capabilities：只读配置状态（布尔），用于前端禁用/提示 */
  getCapabilities: (): Promise<Capabilities> =>
    toJson(fetch(apiUrl('/capabilities')).catch(throwNetworkError)),
  /** GET /health → 后端 GET /api/health（仅 {status}，无配置信息） */
  healthCheck: (signal?: AbortSignal): Promise<HealthResponse> =>
    toJson(fetch(apiUrl('/health'), { signal }).catch(throwNetworkError)),
} as const
