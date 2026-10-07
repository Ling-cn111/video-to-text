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

/**
 * 转写引擎手动选择：local（本地 faster-whisper，默认）/ cloud（云端 OpenAI 兼容接口）。
 * 纯手动选择，无自动切换；云端运行期故障由后端 ASR_ENGINE_FALLBACK 降级本地。
 */
export type TranscribeEngine = 'local' | 'cloud'

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

/**
 * AI 总结契约（阶段三，任务 E）：LLM 生成，keyPoints 时间戳指向原 transcript。
 * Chapter → ChapterNote：{title, timeStart, timeEnd, note}（字段名 note 避免与顶层 summary 混淆）。
 */
export interface KeyPoint {
  /** 秒，取自原 transcript 时间戳（后端就近吸附校验） */
  time: number
  text: string
}

export interface ChapterNote {
  title: string
  /** 秒 */
  timeStart: number
  timeEnd: number
  note: string
}

export interface Summary {
  /** 一句话概要 */
  summary: string
  /** 要点（3-5 条，带原文字稿时间戳） */
  keyPoints: KeyPoint[]
  /** 章节笔记 */
  chapters: ChapterNote[]
}

/** AI 总结请求（无状态）：Mock 只用 taskId，真实后端只用 transcript/title/duration */
export interface SummarizeRequest {
  taskId: string
  transcript: TranscriptItem[]
  title: string
  duration: number
}

/** 能力探测（任务 M2-P2-A）：只含布尔配置状态，后端不返回 Key 的任何信息 */
export interface Capabilities {
  /** 云端 ASR Key（CLOUD_ASR_API_KEY）是否已配置 */
  cloudAsrConfigured: boolean
  /** LLM 总结 Key（按 LLM_PROVIDER 对应的 Key）是否已配置 */
  summarizeConfigured: boolean
}

export type TaskStatus = 'processing' | 'completed' | 'failed' | 'interrupted'

/**
 * 文字稿来源：subtitle_cc（B站 CC 字幕）/ subtitle_ai（B站 AI 字幕，未登录弹幕接口获取）
 * / asr（语音识别兜底）。字幕来源任务在结果页显示「来源：B站字幕」Badge。
 */
export type TranscriptSource = 'subtitle_cc' | 'subtitle_ai' | 'asr'

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
  /** 视频页原始链接（真实后端下载音频用；缺省时由 videoId 按平台规则重建） */
  url?: string
  /**
   * 可选的真实视频元数据透传：接真实后端（M1.5）时解析由 FastAPI 完成，
   * 转写/总结仍为 Mock；带上该字段可让进度页/结果页展示真实标题、封面、时长。
   */
  video?: VideoInfo
  /** 可选转写引擎手动选择，缺省跟随后端 ASR_ENGINE 环境变量 */
  engine?: TranscribeEngine
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
  /** 文字稿来源（字幕来源任务显示来源 Badge）；缺省视为 asr */
  transcriptSource?: TranscriptSource
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
  | 'CLOUD_NOT_CONFIGURED'
  | 'SUMMARIZE_NOT_CONFIGURED'
  | 'TRANSCRIPT_TOO_LONG'
  | 'SUMMARIZE_FAILED'
  | 'TASK_INTERRUPTED'
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
