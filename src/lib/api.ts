import type { ApiErrorCode, ApiErrorBody, GetTaskResponse, StartTaskResponse, Summary, VideoInfo } from '@/lib/types'
import { toSafeHttpUrl } from '@/lib/url-guard'

/**
 * 客户端 API 统一出口。
 *
 * 当前指向同域的 Mock Route Handlers（src/app/api）；
 * 接入真实后端时设置 NEXT_PUBLIC_API_BASE_URL 即可，所有调用方无需改动。
 */
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? ''

/** 客户端可见的错误码 = 契约错误码 + 网络层补充码 */
export type ClientErrorCode = ApiErrorCode | 'NETWORK_ERROR' | 'UNKNOWN_ERROR'

export class ApiError extends Error {
  constructor(
    public code: ClientErrorCode,
    message: string,
    public status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** 解析请求端点：相对路径=同源直连；绝对地址需通过安全校验（仅 http/https 且 host 非保留地址） */
function resolveEndpoint(path: string): string {
  const full = `${API_BASE}${path}`
  if (full.startsWith('/')) return full
  const safe = toSafeHttpUrl(full)
  if (!safe) {
    throw new ApiError('UNKNOWN_ERROR', 'API 地址不可用，请检查 NEXT_PUBLIC_API_BASE_URL 配置', 0)
  }
  return safe.toString()
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(resolveEndpoint(path), {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError('NETWORK_ERROR', '网络异常，请检查网络连接后重试', 0)
  }

  if (!response.ok) {
    let code: ClientErrorCode = 'UNKNOWN_ERROR'
    let message = `请求失败（HTTP ${response.status}）`
    try {
      const body = (await response.json()) as ApiErrorBody
      if (body?.error) {
        code = body.error.code
        message = body.error.message
      }
    } catch {
      // 错误体不是 JSON，保留默认提示
    }
    throw new ApiError(code, message, response.status)
  }

  return response.json() as Promise<T>
}

export const api = {
  /** POST /api/parse：解析视频链接 */
  parseVideo: (url: string) =>
    request<VideoInfo>('/api/parse', { method: 'POST', body: JSON.stringify({ url }) }),

  /** POST /api/transcribe：创建转写任务 */
  startTranscription: (videoId: string) =>
    request<StartTaskResponse>('/api/transcribe', {
      method: 'POST',
      body: JSON.stringify({ videoId }),
    }),

  /** GET /api/transcribe/:taskId：轮询任务状态 / 获取转写结果 */
  getTask: (taskId: string) =>
    request<GetTaskResponse>(`/api/transcribe/${encodeURIComponent(taskId)}`),

  /** POST /api/summarize：生成 AI 总结 */
  summarize: (taskId: string) =>
    request<Summary>('/api/summarize', { method: 'POST', body: JSON.stringify({ taskId }) }),
} as const
