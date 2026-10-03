/**
 * 前后端接口契约（单一事实来源）。
 *
 * 说明：当前 Demo 的 Mock 实现在 `mock-api.ts`，接入真实后端后本文件即接口文档。
 * 为支持进度页轮询，GET 任务接口在原始设计（status/transcript/plainText）之上
 * 补充了 stage / progress / video / error 字段，属向后兼容的超集。
 */

/** 平台标识。当前 Demo 支持 B站 / 抖音，后续按此联合类型扩展新平台。 */
export type Platform = 'bilibili' | 'douyin'

/** 任务来源：链接解析或本地文件（本地文件在 M3 落地，见 docs/ROADMAP.md） */
export type TaskSource = 'url' | 'file'

export interface VideoInfo {
  title: string
  /** 封面图 URL（Demo 为本地占位图） */
  cover: string
  /** 时长（秒） */
  duration: number
  platform: Platform
  videoId: string
}

export interface TranscriptItem {
  /** 相对视频开头的秒数 */
  time: number
  text: string
}

export interface Chapter {
  /** 章节开始时间（秒） */
  time: number
  title: string
  note: string
}

export interface Summary {
  /** 一句话概要 */
  summary: string
  /** 要点（3-5 条） */
  keyPoints: string[]
  /** 章节笔记 */
  chapters: Chapter[]
}

export type TaskStatus = 'processing' | 'completed' | 'failed'

/** 转写流水线阶段 */
export type TaskStage = 'parse_link' | 'extract_audio' | 'asr' | 'summarize'

export interface StageMeta {
  id: TaskStage
  label: string
  description: string
}

/** 四阶段流水线的展示信息 */
export const TASK_STAGES: StageMeta[] = [
  { id: 'parse_link', label: '解析链接', description: '读取视频信息与元数据' },
  { id: 'extract_audio', label: '提取音频', description: '分离音轨并重采样为 16kHz' },
  { id: 'asr', label: '语音识别', description: '识别语音并生成带时间戳的文字' },
  { id: 'summarize', label: '生成总结', description: '提炼概要、要点与章节笔记' },
]

// ---- 接口请求 / 响应 ----

export interface StartTaskRequest {
  videoId: string
}

export interface StartTaskResponse {
  taskId: string
  status: TaskStatus
  stage: TaskStage
  progress: number
}

export interface GetTaskResponse {
  taskId: string
  status: TaskStatus
  stage: TaskStage
  /** 0-100 */
  progress: number
  /** 视频信息（便于结果页刷新后直接恢复展示） */
  video?: VideoInfo
  /** status=completed 时返回 */
  transcript?: TranscriptItem[]
  /** 纯文本全文（无时间戳） */
  plainText?: string
  /** status=failed 时返回 */
  error?: AppError
}

export interface SummarizeRequest {
  taskId: string
}

// ---- 错误契约 ----

export type ApiErrorCode =
  | 'INVALID_URL'
  | 'UNSUPPORTED_PLATFORM'
  | 'INVALID_TASK'
  | 'TRANSCRIBE_FAILED'
  | 'FILE_TOO_LARGE'
  | 'FILE_FORMAT_UNSUPPORTED'
  | 'INTERNAL_ERROR'

export interface AppError {
  code: ApiErrorCode
  message: string
}

/** 所有非 2xx 响应的错误体形状 */
export interface ApiErrorBody {
  error: AppError
}
