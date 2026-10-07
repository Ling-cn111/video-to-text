import { JSON_HEADERS, toJson, throwNetworkError, type ApiClient } from '@/lib/http'
import type {
  Capabilities,
  GetTaskResponse,
  StartTaskResponse,
  SummarizeRequest,
  Summary,
  TranscribeEngine,
  VideoInfo,
} from '@/lib/types'

/**
 * 真实后端客户端（FastAPI + yt-dlp + faster-whisper + LLM 总结，代码见 backend/）。
 *
 * 解析 / 转写 / AI 总结均走真实后端；请求走同源相对路径 /backend-api/*，
 * 由 Next.js rewrites 服务端代理到 BACKEND_ORIGIN 指向的后端（见 next.config.mjs），
 * 浏览器不直接跨域访问后端。切换：NEXT_PUBLIC_USE_MOCK=false 时由 lib/api.ts 选用本客户端。
 */

export const realApi: ApiClient = {
  /** POST /backend-api/parse → 代理到后端 POST /api/parse（yt-dlp 解析） */
  parseVideo: (url: string): Promise<VideoInfo> =>
    toJson(
      fetch('/backend-api/parse', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ url }) }).catch(
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
      fetch('/backend-api/transcribe', {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify({ videoId, video, url, engine }),
      }).catch(throwNetworkError),
    ),

  getTask: (taskId: string): Promise<GetTaskResponse> =>
    toJson(fetch(`/backend-api/transcribe/${encodeURIComponent(taskId)}`).catch(throwNetworkError)),

  /** POST /backend-api/summarize：真实 LLM 总结（无状态：直接传文字稿与元数据，taskId 仅 Mock 用） */
  summarize: (request: SummarizeRequest): Promise<Summary> =>
    toJson(
      fetch('/backend-api/summarize', {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify({
          transcript: request.transcript,
          title: request.title,
          duration: request.duration,
        }),
      }).catch(throwNetworkError),
    ),
  /** GET /backend-api/capabilities：只读配置状态（布尔），用于前端禁用/提示 */
  getCapabilities: (): Promise<Capabilities> =>
    toJson(fetch('/backend-api/capabilities').catch(throwNetworkError)),
} as const
