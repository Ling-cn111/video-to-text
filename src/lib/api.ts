import { ApiError, JSON_HEADERS, toJson, throwNetworkError, type ApiClient, type ClientErrorCode } from '@/lib/http'
import { HEADER_LLM_KEY, byokBodyFor, byokHeaderFor } from '@/lib/byok'
import { realApi } from '@/lib/real-api'
import type {
  ByokTestResult,
  Capabilities,
  FileRef,
  FramesRequest,
  FramesStartResponse,
  GetTaskResponse,
  HealthResponse,
  StartTaskResponse,
  SummarizeRequest,
  Summary,
  TranscribeEngine,
  UploadInitRequest,
  VideoInfo,
} from '@/lib/types'

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

  /**
   * POST /api/transcribe：创建转写任务。
   *
   * url / engine / hotwords 三个参数 Mock 都不使用（Mock 的 taskId 自带上下文），
   * 但仍要原样发出去：E2E 靠拦截这个请求体来证明「热词真的被带上了」——如果这里
   * 悄悄丢掉字段，Mock 模式下测不到、真实模式下才炸，是最难查的那类问题。
   */
  startTranscription: (
    videoId: string,
    video?: VideoInfo,
    url?: string,
    engine?: TranscribeEngine,
    hotwords?: string[],
  ): Promise<StartTaskResponse> =>
    toJson(
      fetch('/api/transcribe', {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify({ videoId, video, url, engine, hotwords }),
      }).catch(throwNetworkError),
    ),

  /** GET /api/transcribe/:taskId：轮询任务状态 / 获取转写结果 */
  getTask: (taskId: string): Promise<GetTaskResponse> =>
    toJson(fetch(`/api/transcribe/${encodeURIComponent(taskId)}`).catch(throwNetworkError)),

  /** POST /api/summarize：生成 AI 总结（Mock 只用 taskId，其余字段透传给真实后端用） */
  summarize: (request: SummarizeRequest): Promise<Summary> =>
    toJson(
      fetch('/api/summarize', {
        method: 'POST',
        // Mock 路由不读这个头，但要保证「填了 Key → 请求头带上」这条链路可被测到
        headers: { ...JSON_HEADERS, ...byokHeaderFor() },
        // 地址与模型走请求体，同样为了这条链路在 Mock 模式下可被测到
        body: JSON.stringify({ ...request, ...byokBodyFor() }),
      }).catch(throwNetworkError),
    ),
  /**
   * M3 上传三步（Mock）：init 走同源 Mock 路由（校验规则与真实后端一致）；
   * 直传**不真发网络**——uploadUrl 是假地址，这里按固定节奏走完模拟进度，
   * E2E 据此断言进度条可见；发起转写与真实后端同一请求形状（kind=file）。
   */
  initFileUpload: (request: UploadInitRequest): Promise<{ uploadUrl: string; objectKey: string; expiresIn: number }> =>
    toJson(
      fetch('/api/upload/init', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(request) }).catch(
        throwNetworkError,
      ),
    ),

  uploadFileToOss: async (
    file: File,
    _uploadUrl: string,
    onProgress?: (percent: number) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    // Mock 上传约 2.4 秒走完（8 步）：给 E2E 留出断言「进度条出现」的窗口，
    // 也让人工演示能看清进度在动。abort 时立刻结束（模拟取消）。
    for (const percent of [12, 25, 40, 55, 70, 85, 95, 100]) {
      if (signal?.aborted) throw new ApiError('NETWORK_ERROR', '上传已取消', 0)
      await new Promise((resolve) => setTimeout(resolve, 300))
      onProgress?.(percent)
    }
  },

  startFileTranscription: (fileRef: FileRef, engine?: TranscribeEngine, hotwords?: string[]): Promise<StartTaskResponse> =>
    toJson(
      fetch('/api/transcribe', {
        method: 'POST',
        headers: JSON_HEADERS,
        // Mock 服务端按 kind=file 合成 VideoInfo（标题=文件名）；engine/hotwords
        // 照发：E2E 靠拦截请求体证明字段真的带上了（与 startTranscription 同理）
        body: JSON.stringify({ kind: 'file', file: fileRef, engine, hotwords }),
      }).catch(throwNetworkError),
    ),

  /**
   * M3-BYOK 测活（Mock）：走同源 Mock 路由。失败注入约定：模型名含「invalid」
   * 时返回 BYOK_KEY_INVALID，其余恒成功（延迟固定 123ms 便于断言）。
   * Key 随请求头照发：E2E 可断言「测试用的是草稿里的 Key」。
   */
  testLlmConfig: (request: { llmBaseUrl: string; llmModel: string }, key: string): Promise<ByokTestResult> =>
    toJson(
      fetch('/api/byok/test', {
        method: 'POST',
        headers: { ...JSON_HEADERS, ...(key ? { [HEADER_LLM_KEY]: key } : {}) },
        body: JSON.stringify(request),
      }).catch(throwNetworkError),
    ),

  /**
   * PR #89 重点帧（Mock）：走同源 Mock 路由，ready 随响应立即返回（无状态）。
   * timestamps 照发：E2E 靠拦截请求体断言「时间点真的来自总结章节/要点」。
   */
  requestFrames: (request: FramesRequest): Promise<FramesStartResponse> =>
    toJson(
      fetch('/api/frames', {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify(request),
      }).catch(throwNetworkError),
    ),

  /** GET /api/capabilities：Mock 模式恒为已配置（不阻塞演示） */
  getCapabilities: (): Promise<Capabilities> =>
    toJson(fetch('/api/capabilities').catch(throwNetworkError)),

  /** GET /api/health：Mock 模式同源健康检查（恒 200，供 E2E 拦截制造失败） */
  healthCheck: (signal?: AbortSignal): Promise<HealthResponse> =>
    toJson(fetch('/api/health', { signal }).catch(throwNetworkError)),
} as const

const useMock = process.env.NEXT_PUBLIC_USE_MOCK !== 'false'

export const api = useMock ? mockApi : realApi
