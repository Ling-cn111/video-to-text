import { resolveVideoUrl } from '@/lib/platform'
import { getPlatformMeta, unsupportedHostMessage } from '@/lib/platforms/registry'
import { FILE_ALLOWED_EXTENSIONS, FILE_MAX_SIZE_MB } from '@/lib/file-upload'
import { FAIL_VIDEO_ID, getMockData } from '@/lib/mock-data'
import type {
  ApiErrorCode,
  FileRef,
  FramesStartResponse,
  GetTaskResponse,
  Platform,
  StartTaskResponse,
  Summary,
  SummaryDetail,
  TaskStage,
  TaskStatus,
  UploadInitRequest,
  UploadInitResponse,
  VideoInfo,
} from '@/lib/types'

/**
 * Mock 服务层：无状态实现，可直接部署到 serverless。
 *
 * - taskId = base64url(JSON { videoId, platform, startedAt, fail? })，自带全部上下文
 * - 进度由 now - startedAt 推算（全程约 10 秒），无需存储
 * - 文字稿 / 总结由平台确定性映射，多次调用结果一致
 */

const TASK_TOTAL_MS = 10_000
/** 失败演示：进行到 60% 时任务失败 */
const FAIL_AT_MS = 6_000

/** Mock 服务端错误，Route Handler 捕获后映射为对应 HTTP 状态码 */
export class MockApiError extends Error {
  constructor(
    public code: ApiErrorCode,
    message: string,
  ) {
    super(message)
    this.name = 'MockApiError'
  }
}

// ---- 任务编码 ----

interface TaskPayload {
  /** videoId */
  v: string
  /** platform */
  p: Platform
  /** startedAt（毫秒时间戳） */
  s: number
  /** 注入失败标记 */
  f?: 1
  /** 注入总结失败标记（E2E 锁定 8：验证总结失败降级不拖垮文字稿） */
  sumFail?: 1
  /** 注入重点帧失败标记（E2E 回归 40c：帧卡片错误态与重试） */
  fr?: 1
  /** 文字稿来源标记（E2E 锁定 5 用：字幕来源显示「来源：B站字幕」，asr 显示「来源：语音识别」） */
  src?: 'subtitle_cc' | 'subtitle_ai' | 'asr'
  /** 真实解析透传的元数据（M1.5 混合模式：解析真实、转写 Mock） */
  m?: { t: string; c: string; d: number }
}

function encodeTaskId(payload: TaskPayload): string {
  return Buffer.from(JSON.stringify(payload), 'utf8').toString('base64url')
}

function decodeTaskId(taskId: string): TaskPayload | null {
  try {
    const payload = JSON.parse(Buffer.from(taskId, 'base64url').toString('utf8')) as TaskPayload
    if (typeof payload?.v !== 'string' || typeof payload?.p !== 'string' || typeof payload?.s !== 'number') {
      return null
    }
    return payload
  } catch {
    return null
  }
}

// ---- 进度模型：四阶段推进 ----

const STAGE_RANGES: Array<{ stage: TaskStage; from: number; to: number }> = [
  { stage: 'parse_link', from: 0, to: 0.15 },
  { stage: 'extract_audio', from: 0.15, to: 0.4 },
  { stage: 'asr', from: 0.4, to: 0.85 },
  { stage: 'summarize', from: 0.85, to: 1 },
]

interface TaskState {
  status: TaskStatus
  stage: TaskStage
  progress: number
}

function computeTaskState(payload: TaskPayload): TaskState {
  const elapsed = Date.now() - payload.s

  if (payload.f && elapsed >= FAIL_AT_MS) {
    return { status: 'failed', stage: 'asr', progress: 60 }
  }

  const ratio = Math.min(Math.max(elapsed / TASK_TOTAL_MS, 0), 1)
  if (ratio >= 1) {
    return { status: 'completed', stage: 'summarize', progress: 100 }
  }

  const range = STAGE_RANGES.find(({ from, to }) => ratio >= from && ratio < to) ?? STAGE_RANGES[0]
  return { status: 'processing', stage: range.stage, progress: Math.floor(ratio * 100) }
}

function getVideoInfo(payload: TaskPayload): VideoInfo {
  if (payload.m) {
    return {
      platform: payload.p,
      videoId: payload.v,
      title: payload.m.t,
      cover: payload.m.c,
      duration: payload.m.d,
    }
  }
  return {
    platform: payload.p,
    videoId: payload.v,
    ...getMockData(payload.p).video,
  }
}

/** 由 videoId 推断平台（Mock 专用：BV/av → B站，纯数字长串 → 抖音，默认 B站） */
function detectPlatformByVideoId(videoId: string): Platform {
  if (/^(BV|av)\w*/i.test(videoId)) return 'bilibili'
  if (/^\d{10,}$/.test(videoId)) return 'douyin'
  return 'bilibili'
}

// ---- 对外接口（与真实后端一一对应） ----

export async function parseVideoUrl(rawUrl: string): Promise<VideoInfo> {
  if (!rawUrl?.trim()) {
    throw new MockApiError('INVALID_URL', '请输入视频链接')
  }

  const result = resolveVideoUrl(rawUrl)
  if (!result.ok) {
    if (result.reason === 'invalid') {
      throw new MockApiError('INVALID_URL', '链接格式不正确，请粘贴完整的视频页面链接')
    }
    throw new MockApiError('UNSUPPORTED_PLATFORM', unsupportedHostMessage(result.host))
  }

  return {
    platform: result.platform,
    videoId: result.videoId,
    ...getMockData(result.platform).video,
  }
}

export async function startTranscription(
  videoId: string,
  video?: VideoInfo,
  /** M3：kind=file 时走文件任务分支（videoId 由 objectKey 顶替） */
  options?: { kind?: 'url' | 'file'; file?: FileRef },
): Promise<StartTaskResponse> {
  if (options?.kind === 'file') {
    return startFileTask(options.file, video)
  }
  if (!videoId?.trim()) {
    throw new MockApiError('INVALID_URL', '缺少 videoId，请先解析视频链接')
  }

  const payload: TaskPayload = {
    v: videoId,
    p: detectPlatformByVideoId(videoId),
    s: Date.now(),
  }
  if (videoId === FAIL_VIDEO_ID) payload.f = 1
  if (video?.title) {
    payload.m = { t: video.title, c: video.cover, d: video.duration }
  }

  return { taskId: encodeTaskId(payload), ...computeTaskState(payload) }
}

/**
 * file 任务分支（M3）：与真实后端同规则——objectKey 必填、合成 VideoInfo
 * （platform=local、无封面、时长 0 由「Worker」回填，Mock 不模拟回填）。
 */
async function startFileTask(fileRef: FileRef | undefined, video?: VideoInfo): Promise<StartTaskResponse> {
  if (!fileRef?.objectKey?.trim()) {
    throw new MockApiError('FILE_NOT_FOUND', '上传的文件还未就绪，请等上传完成后再发起转写')
  }
  const resolved: VideoInfo =
    video ?? {
      title: fileRef.fileName || '本地视频',
      cover: '',
      duration: 0,
      platform: 'local',
      videoId: fileRef.objectKey,
    }
  const payload: TaskPayload = {
    v: fileRef.objectKey,
    p: 'local',
    s: Date.now(),
    m: { t: resolved.title, c: resolved.cover, d: resolved.duration },
  }
  return { taskId: encodeTaskId(payload), ...computeTaskState(payload) }
}

/**
 * M3 上传凭证签发（Mock）：与真实 /api/upload/init 同一套校验与响应形状，
 * uploadUrl 是**假地址**——Mock 客户端的 uploadFileToOss 不会真发网络，
 * 该值仅用于 E2E 断言「init 请求体 / 响应形状」。
 */
export async function initFileUpload(request: UploadInitRequest): Promise<UploadInitResponse> {
  const fileName = (request?.fileName ?? '').trim()
  const extension = fileName.includes('.') ? fileName.split('.').pop()!.toLowerCase() : ''
  if (!FILE_ALLOWED_EXTENSIONS.includes(extension as (typeof FILE_ALLOWED_EXTENSIONS)[number])) {
    throw new MockApiError(
      'FILE_FORMAT_UNSUPPORTED',
      `暂不支持「${extension || '无扩展名'}」格式，请上传 mp4 / mov / webm 视频`,
    )
  }
  const size = request?.size ?? 0
  if (size <= 0 || size > FILE_MAX_SIZE_MB * 1024 * 1024) {
    throw new MockApiError('FILE_TOO_LARGE', `文件超过 ${FILE_MAX_SIZE_MB}MB 上限，请压缩或裁剪后再上传`)
  }
  const objectKey = `uploads/${Buffer.from(fileName).toString('hex').slice(0, 32)}/${fileName}`
  return {
    uploadUrl: `/mock-oss/${encodeURIComponent(objectKey)}`,
    objectKey,
    expiresIn: 7200,
  }
}

export async function getTask(taskId: string): Promise<GetTaskResponse> {
  const payload = decodeTaskId(taskId)
  if (!payload) {
    throw new MockApiError('INVALID_TASK', '任务不存在或已过期，请重新解析视频链接')
  }

  const state = computeTaskState(payload)
  const base = { taskId, video: getVideoInfo(payload), ...state, transcriptSource: payload.src }

  if (state.status === 'failed') {
    return { ...base, error: { code: 'TRANSCRIBE_FAILED', message: '语音识别失败，请重试' } }
  }
  if (state.status === 'completed') {
    const { transcript } = getMockData(payload.p)
    return { ...base, transcript, plainText: transcript.map((item) => item.text).join('') }
  }
  return base
}

/** Mock 的档位裁剪：与后端 DETAIL_PRESETS 的 maxKeyPoints 保持一致，便于 E2E 断言「档位真的生效」 */
const MOCK_DETAIL_MAX_POINTS: Record<SummaryDetail, number> = {
  brief: 3,
  standard: 5,
  detailed: 7,
}

export async function summarizeTask(taskId: string, detail: SummaryDetail = 'standard'): Promise<Summary> {
  const payload = decodeTaskId(taskId)
  if (!payload) {
    // Mock 模式下所有任务均为本模块编码的 taskId；无法解码视为非法请求
    throw new MockApiError('INVALID_TASK', '任务不存在或已过期，请重新解析视频链接')
  }
  if (payload.sumFail) {
    // E2E 锁定 8：模拟总结失败（验证 P0 降级——文字稿照常渲染 + 总结区错误与重试）
    throw new MockApiError('SUMMARIZE_FAILED', 'AI 总结生成失败，请稍后重试')
  }
  const summary = getMockData(payload.p).summary
  const limit = MOCK_DETAIL_MAX_POINTS[detail] ?? MOCK_DETAIL_MAX_POINTS.standard
  // brief 档连章节也收敛：只留前两段，模拟「精简」在真实 LLM 下的表现
  const chapters = detail === 'brief' ? summary.chapters.slice(0, 2) : summary.chapters
  return { ...summary, keyPoints: summary.keyPoints.slice(0, limit), chapters }
}

// ---- 重点帧（PR #89） ----

/** Mock 帧数上限：与后端 FRAMES_MAX_COUNT 同意图，Mock 取小值让演示网格轻量 */
const MOCK_FRAMES_MAX = 8
/** 与后端 sanitize_timestamps 的 2s 近邻合并同口径 */
const MOCK_FRAME_MIN_GAP_SECONDS = 2

/**
 * 重点帧请求（Mock）：无状态，**ready 随响应立即返回**（真实后端是
 * processing → 轮询任务详情 → ready，processing 阶段的 E2E 覆盖走
 * Playwright 路由拦截，不依赖 Mock 模拟时序）。
 *
 * 校验与真实后端同规则：
 * - 任务不存在 → INVALID_TASK；未完成转写 → FRAMES_NOT_READY
 * - 本地文件任务 → FRAMES_UNSUPPORTED（真实后端源对象成功即删，无源可抽）
 * - 注入失败标记（编造 taskId 的 fr:1）→ frames.status=failed（回归 40c）
 */
export async function requestFrames(taskId: string, timestamps: number[]): Promise<FramesStartResponse> {
  const payload = decodeTaskId(taskId)
  if (!payload) {
    throw new MockApiError('INVALID_TASK', '任务不存在或已过期，请重新解析视频链接')
  }
  const state = computeTaskState(payload)
  if (state.status !== 'completed') {
    throw new MockApiError('FRAMES_NOT_READY', '任务尚未完成转写，完成后再生成重点帧')
  }
  if (payload.p === 'local') {
    throw new MockApiError('FRAMES_UNSUPPORTED', '本地文件任务暂不支持生成重点帧')
  }
  if (payload.fr) {
    return {
      taskId,
      frames: {
        status: 'failed',
        error: { code: 'FRAMES_FAILED', message: '视频流下载失败，请稍后重试' },
        items: [],
      },
    }
  }

  // 时间点净化与后端 sanitize_timestamps 同口径：非法丢弃、升序、2s 近邻合并、截断
  const points: number[] = []
  for (const value of [...(timestamps ?? [])].sort((a, b) => a - b)) {
    const point = Number(value)
    if (!Number.isFinite(point) || point < 0) continue
    if (points.length > 0 && point - points[points.length - 1] < MOCK_FRAME_MIN_GAP_SECONDS) continue
    points.push(Math.round(point * 100) / 100)
    if (points.length >= MOCK_FRAMES_MAX) break
  }
  // 帧图占位直接复用平台封面 SVG（Mock 无真实抽帧；E2E 断言网格与时间标签，
  // 不断言图片内容）。objectKey 仅形状示例，Mock 下不参与任何 OSS 语义。
  const cover = getMockData(payload.p).video.cover
  return {
    taskId,
    frames: {
      status: 'ready',
      items: points.map((time, index) => ({
        time,
        objectKey: `frames/mock/${String(index).padStart(2, '0')}.jpg`,
        url: cover,
      })),
    },
  }
}

export { getPlatformMeta }
