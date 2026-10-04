import { ApiError, JSON_HEADERS, toJson, throwNetworkError, type ApiClient, type ClientErrorCode } from '@/lib/http'
import { realApi } from '@/lib/real-api'
import type { GetTaskResponse, StartTaskResponse, Summary, VideoInfo } from '@/lib/types'

export { ApiError, type ClientErrorCode }

/**
 * 客户端 API 统一出口（调用方只 import 这里的 api，不感知实现）。
 *
 * - 默认（NEXT_PUBLIC_USE_MOCK !== 'false'）：指向同域 Mock Route Handlers（src/app/api）
 * - NEXT_PUBLIC_USE_MOCK=false：解析走真实后端（lib/real-api.ts，FastAPI + yt-dlp），
 *   转写/总结暂仍由 Mock 承担（见 real-api.ts 的 M1.5 说明）
 *
 * 请求目标均为编译期字面量路径（真实后端经 /backend-api/* 同源代理，见 next.config.mjs）。
 */

const mockApi: ApiClient = {
  /** POST /api/parse：解析视频链接 */
  parseVideo: (url: string): Promise<VideoInfo> =>
    toJson(
      fetch('/api/parse', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ url }) }).catch(
        throwNetworkError,
      ),
    ),

  /** POST /api/transcribe：创建转写任务（url 参数仅在真实模式使用，Mock 忽略） */
  startTranscription: (videoId: string, video?: VideoInfo): Promise<StartTaskResponse> =>
    toJson(
      fetch('/api/transcribe', {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify({ videoId, video }),
      }).catch(throwNetworkError),
    ),

  /** GET /api/transcribe/:taskId：轮询任务状态 / 获取转写结果 */
  getTask: (taskId: string): Promise<GetTaskResponse> =>
    toJson(fetch(`/api/transcribe/${encodeURIComponent(taskId)}`).catch(throwNetworkError)),

  /** POST /api/summarize：生成 AI 总结 */
  summarize: (taskId: string): Promise<Summary> =>
    toJson(
      fetch('/api/summarize', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ taskId }) }).catch(
        throwNetworkError,
      ),
    ),
} as const

const useMock = process.env.NEXT_PUBLIC_USE_MOCK !== 'false'

export const api = useMock ? mockApi : realApi
