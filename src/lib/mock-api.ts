import { resolveVideoUrl } from '@/lib/platform'
import { getPlatformMeta } from '@/lib/platforms/registry'
import { FAIL_VIDEO_ID, getMockData } from '@/lib/mock-data'
import type {
  ApiErrorCode,
  GetTaskResponse,
  Platform,
  StartTaskResponse,
  Summary,
  TaskStage,
  TaskStatus,
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
  /** 文字稿来源标记（E2E 锁定 5 用：字幕来源任务显示来源 Badge） */
  src?: 'subtitle_cc' | 'subtitle_ai'
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
    throw new MockApiError(
      'UNSUPPORTED_PLATFORM',
      `暂不支持「${result.host}」平台，当前支持哔哩哔哩、抖音`,
    )
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
): Promise<StartTaskResponse> {
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

export async function summarizeTask(taskId: string): Promise<Summary> {
  const payload = decodeTaskId(taskId)
  if (!payload) {
    // Mock 模式下所有任务均为本模块编码的 taskId；无法解码视为非法请求
    throw new MockApiError('INVALID_TASK', '任务不存在或已过期，请重新解析视频链接')
  }
  if (payload.sumFail) {
    // E2E 锁定 8：模拟总结失败（验证 P0 降级——文字稿照常渲染 + 总结区错误与重试）
    throw new MockApiError('SUMMARIZE_FAILED', 'AI 总结生成失败，请稍后重试')
  }
  return getMockData(payload.p).summary
}

export { getPlatformMeta }
