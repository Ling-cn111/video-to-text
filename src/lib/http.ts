import type {
  ApiErrorCode,
  ApiErrorBody,
  ByokTestResult,
  Capabilities,
  FileRef,
  GetTaskResponse,
  HealthResponse,
  StartTaskResponse,
  SummarizeRequest,
  Summary,
  TranscribeEngine,
  UploadInitRequest,
  UploadInitResponse,
  VideoInfo,
} from '@/lib/types'

/**
 * HTTP 客户端共享类型与错误解析。
 *
 * 各客户端方法内联字面量路径直接 fetch（请求目标为编译期常量，不含 URL 污点流）；
 * 本模块只处理与 Response 相关的错误解析，不接触 URL。
 */

/** 客户端 API 形状契约：mock 与 real 实现均满足此接口 */
export interface ApiClient {
  parseVideo: (url: string) => Promise<VideoInfo>
  startTranscription: (
    videoId: string,
    video?: VideoInfo,
    url?: string,
    engine?: TranscribeEngine,
    hotwords?: string[],
  ) => Promise<StartTaskResponse>
  /**
   * M3 本地文件上传三步：init（签发凭证）→ 直传 OSS（字节不经后端）→ 创建 file 任务。
   * mock 实现不真发网络（假 URL + 模拟进度）；real 实现见 real-api.ts。
   */
  initFileUpload: (request: UploadInitRequest) => Promise<UploadInitResponse>
  /** 直传文件到 uploadUrl（预签名 PUT）；onProgress 报告 0-100 百分比 */
  uploadFileToOss: (
    file: File,
    uploadUrl: string,
    onProgress?: (percent: number) => void,
    signal?: AbortSignal,
  ) => Promise<void>
  startFileTranscription: (
    fileRef: FileRef,
    engine?: TranscribeEngine,
    hotwords?: string[],
  ) => Promise<StartTaskResponse>
  getTask: (taskId: string) => Promise<GetTaskResponse>
  summarize: (request: SummarizeRequest) => Promise<Summary>
  /**
   * M3-BYOK：测活——用当前草稿（地址 + 模型）与指定 Key 发一次极短真实请求。
   * key 由调用方显式传入（测的是草稿，不一定是已保存的激活配置）。
   */
  testLlmConfig: (
    request: { llmBaseUrl: string; llmModel: string },
    key: string,
  ) => Promise<ByokTestResult>
  /** 能力探测（只读布尔，不含 Key） */
  getCapabilities: () => Promise<Capabilities>
  /** 健康检查（只读轻量探测）：失败 / 超时 / 非 2xx 均视为后端不可达 */
  healthCheck: (signal?: AbortSignal) => Promise<HealthResponse>
}

/** 客户端可见的错误码 = 契约错误码 + 网络层补充码 */
export type ClientErrorCode = ApiErrorCode | 'NETWORK_ERROR' | 'UNKNOWN_ERROR'

export class ApiError extends Error {
  constructor(
    public code: ClientErrorCode,
    message: string,
    public status: number,
    /** 服务端建议的重试等待秒数（429 的 Retry-After）；缺省表示服务端没给 */
    public retryAfter?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export const JSON_HEADERS = { 'Content-Type': 'application/json' }

/** 网络层错误归一化：包住 fetch 本身抛出的异常 */
export function throwNetworkError(error: unknown): never {
  if (error instanceof ApiError) throw error
  throw new ApiError('NETWORK_ERROR', '网络异常，请检查网络连接后重试', 0)
}

/**
 * 读取 429 的 `Retry-After`（秒）。
 *
 * 为什么不用固定值：后端是令牌桶，按 `per_minute/60` 每秒回填，实际等到「攒够
 * 1 个令牌」的时间取决于当时的配额，写死 60 秒会让用户白等（默认 10 次/分钟时
 * 只需 6 秒）。服务端既然给了准确值就用它，只在缺失时由调用方兜底。
 * 同时容忍 HTTP-date 格式（RFC 9110 允许），解析失败返回 undefined。
 */
function parseRetryAfter(response: Response): number | undefined {
  const raw = response.headers.get('retry-after')
  if (!raw) return undefined
  const seconds = Number(raw)
  if (Number.isFinite(seconds) && seconds >= 0) return Math.ceil(seconds)
  const at = Date.parse(raw)
  if (!Number.isNaN(at)) {
    const delta = Math.ceil((at - Date.now()) / 1000)
    return delta > 0 ? delta : 0
  }
  return undefined
}

/** 非 2xx 响应 → ApiError（解析 { error: { code, message } } 形状） */
export async function parseErrorResponse(
  response: Response,
  fallbackCode: ClientErrorCode = 'UNKNOWN_ERROR',
): Promise<ApiError> {
  let code: ClientErrorCode = fallbackCode
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
  return new ApiError(code, message, response.status, parseRetryAfter(response))
}

/** 响应 → 数据；请求目标由调用方以字面量路径给出 */
export async function toJson<T>(response: Promise<Response>): Promise<T> {
  let response_: Response
  try {
    response_ = await response
  } catch (error) {
    throwNetworkError(error)
  }
  if (!response_.ok) throw await parseErrorResponse(response_)
  return response_.json() as Promise<T>
}

/**
 * 预签名 PUT 直传（M3）：XHR 而不是 fetch——只有 XHR 的 upload.onprogress
 * 能给出真实上传进度。为什么用 fetch/XHR 直连 OSS：字节不经后端（FC 请求体
 * 上限 32MB），uploadUrl 由后端 /api/upload/init 签发（唯一来自网络的 URL）。
 *
 * Content-Type 必须与签名一致：init 时把 mimeType 传给后端参与签名，这里
 * 显式设置同一个值；file.type 为空（少数浏览器/扩展名）两边都省略。
 */
export function putFileToUrl(
  uploadUrl: string,
  file: File,
  mimeType: string,
  onProgress?: (percent: number) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', uploadUrl)
    if (mimeType) xhr.setRequestHeader('Content-Type', mimeType)
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)))
      }
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve()
      } else {
        reject(new ApiError('NETWORK_ERROR', `文件上传失败（HTTP ${xhr.status}）`, xhr.status))
      }
    }
    xhr.onerror = () => reject(new ApiError('NETWORK_ERROR', '网络异常，文件上传失败', 0))
    xhr.onabort = () => reject(new ApiError('NETWORK_ERROR', '上传已取消', 0))
    // 上传可达 500MB / 数十分钟：必须支持取消（进度条上的取消按钮）
    signal?.addEventListener('abort', () => xhr.abort(), { once: true })
    xhr.send(file)
  })
}
