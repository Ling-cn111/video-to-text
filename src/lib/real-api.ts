import { JSON_HEADERS, toJson, throwNetworkError } from '@/lib/http'
import type { GetTaskResponse, StartTaskResponse, Summary, VideoInfo } from '@/lib/types'

/**
 * 真实后端客户端（FastAPI + yt-dlp，代码见 backend/）。
 *
 * M1.5 范围：仅「解析」接真实后端；转写与总结仍走内置 Mock（同域 Route Handlers），
 * 并把真实解析出的视频元数据透传给 Mock 任务，保证进度页/结果页展示真实标题、封面、时长。
 *
 * 请求走同源相对路径 /backend-api/*，由 Next.js rewrites 服务端代理到
 * BACKEND_ORIGIN 指向的后端（见 next.config.mjs），浏览器不直接跨域访问后端。
 *
 * 切换：NEXT_PUBLIC_USE_MOCK=false 时由 lib/api.ts 选用本客户端。
 */

export const realApi = {
  /** POST /backend-api/parse → 代理到后端 POST /api/parse（yt-dlp 解析） */
  parseVideo: (url: string): Promise<VideoInfo> =>
    toJson(
      fetch('/backend-api/parse', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ url }) }).catch(
        throwNetworkError,
      ),
    ),

  startTranscription: (videoId: string, video?: VideoInfo): Promise<StartTaskResponse> =>
    toJson(
      fetch('/api/transcribe', {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify({ videoId, video }),
      }).catch(throwNetworkError),
    ),

  getTask: (taskId: string): Promise<GetTaskResponse> =>
    toJson(fetch(`/api/transcribe/${encodeURIComponent(taskId)}`).catch(throwNetworkError)),

  summarize: (taskId: string): Promise<Summary> =>
    toJson(
      fetch('/api/summarize', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ taskId }) }).catch(
        throwNetworkError,
      ),
    ),
} as const
