import { JSON_HEADERS, toJson, throwNetworkError, type ApiClient } from '@/lib/http'
import type { GetTaskResponse, StartTaskResponse, Summary, TranscribeEngine, VideoInfo } from '@/lib/types'

/**
 * 真实后端客户端（FastAPI + yt-dlp + faster-whisper，代码见 backend/）。
 *
 * M2 阶段一范围：解析与转写走真实后端；AI 总结仍为内置 Mock（同域 Route Handlers）。
 * 请求走同源相对路径 /backend-api/*，由 Next.js rewrites 服务端代理到
 * BACKEND_ORIGIN 指向的后端（见 next.config.mjs），浏览器不直接跨域访问后端。
 *
 * 切换：NEXT_PUBLIC_USE_MOCK=false 时由 lib/api.ts 选用本客户端。
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

  summarize: (taskId: string): Promise<Summary> =>
    toJson(
      fetch('/api/summarize', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ taskId }) }).catch(
        throwNetworkError,
      ),
    ),
} as const
