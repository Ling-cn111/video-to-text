import { apiUrl } from '@/lib/api-base'
import { byokBodyFor, byokHeaderFor } from '@/lib/byok'
import { JSON_HEADERS, putFileToUrl, toJson, throwNetworkError, type ApiClient } from '@/lib/http'
import type {
  Capabilities,
  FileRef,
  GetTaskResponse,
  HealthResponse,
  StartTaskResponse,
  SummarizeRequest,
  Summary,
  TranscribeEngine,
  UploadInitRequest,
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
    hotwords?: string[],
  ): Promise<StartTaskResponse> =>
    toJson(
      fetch(apiUrl('/transcribe'), {
        method: 'POST',
        // 云端 ASR 用的是站点的免费模型 Key，界面不再收集用户自带的 ASR Key
        headers: JSON_HEADERS,
        // hotwords 为空数组时不发这个字段：后端 schema 是可选的，省略等价于「没填」，
        // 而 [] 虽然也能通过校验，但属数据库/日志里多一个无意义的键
        body: JSON.stringify({
          videoId,
          video,
          url,
          engine,
          ...(hotwords && hotwords.length > 0 ? { hotwords } : {}),
        }),
      }).catch(throwNetworkError),
    ),

  /**
   * M3 上传三步（真实后端）：init 走同源代理 / 桌面直连（只传元数据，32MB 请求体
   * 限制碰不到）；PUT 直连 OSS（XHR 带真实进度）；发起 file 任务只带引用。
   */
  initFileUpload: (request: UploadInitRequest): Promise<{ uploadUrl: string; objectKey: string; expiresIn: number }> =>
    toJson(
      fetch(apiUrl('/upload/init'), { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(request) }).catch(
        throwNetworkError,
      ),
    ),

  uploadFileToOss: (file: File, uploadUrl: string, onProgress?: (percent: number) => void, signal?: AbortSignal) =>
    // Content-Type 必须与后端签名一致：init 传的 mimeType 就是 file.type
    putFileToUrl(uploadUrl, file, file.type ?? '', onProgress, signal),

  startFileTranscription: (fileRef: FileRef, engine?: TranscribeEngine, hotwords?: string[]): Promise<StartTaskResponse> =>
    toJson(
      fetch(apiUrl('/transcribe'), {
        method: 'POST',
        headers: JSON_HEADERS,
        // file 引用是唯一身份信息：后端 HEAD 复核对象存在性与实际大小。
        // hotwords 为空数组时不发该字段（与 startTranscription 的口径一致）
        body: JSON.stringify({
          kind: 'file',
          file: fileRef,
          engine,
          ...(hotwords && hotwords.length > 0 ? { hotwords } : {}),
        }),
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
