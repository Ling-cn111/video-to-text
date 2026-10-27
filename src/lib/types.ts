/**
 * 前后端接口契约（单一事实来源）。
 *
 * 说明：当前 Demo 的 Mock 实现在 `mock-api.ts`，接入真实后端后本文件即接口文档。
 * 为支持进度页轮询，GET 任务接口在原始设计（status/transcript/plainText）之上
 * 补充了 stage / progress / video / error 字段，属向后兼容的超集。
 */

/**
 * 平台标识。这是前端「认得」的域名集合（识别链接、取 videoId 用），
 * **不等于对外宣称的支持范围** —— 后者见 `src/lib/platforms/registry.ts` 的
 * `SUPPORTED_PLATFORMS`（真实后端只接了 B站，抖音目前仅 Mock 演示可用）。
 */
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

/**
 * AI 总结详细度（P1-11）：三档，对应后端 DETAIL_PRESETS。
 * 为什么是三档而不是字数滑块：用户对总结只有三种诉求——一句话结论 / 够用 / 能替代看视频，
 * 连续参数既难解释也难验证。
 */
export type SummaryDetail = 'brief' | 'standard' | 'detailed'

export const SUMMARY_DETAILS: SummaryDetail[] = ['brief', 'standard', 'detailed']

export const SUMMARY_DETAIL_LABEL: Record<SummaryDetail, string> = {
  brief: '精简',
  standard: '标准',
  detailed: '详细',
}

export const SUMMARY_DETAIL_HINT: Record<SummaryDetail, string> = {
  brief: '2-3 条要点，只给结论',
  standard: '3-5 条要点，适合快速了解',
  detailed: '5-7 条要点，保留细节与例证',
}

/** AI 总结请求（无状态）：Mock 只用 taskId，真实后端只用 transcript/title/duration/detail */
export interface SummarizeRequest {
  taskId: string
  transcript: TranscriptItem[]
  title: string
  duration: number
  /** 详细度，缺省 standard（老调用方不传也兼容） */
  detail?: SummaryDetail
}

/** 健康检查响应（GET /api/health）：仅可达性探测，不含任何配置信息 */
export interface HealthResponse {
  status: string
}

/** 能力探测（任务 M2-P2-A）：只含布尔状态，后端不返回 Key 的任何信息 */
export interface Capabilities {
  /** 云端 ASR Key（CLOUD_ASR_API_KEY）是否已配置 */
  cloudAsrConfigured: boolean
  /** LLM 总结 Key（按 LLM_PROVIDER 对应的 Key）是否已配置 */
  summarizeConfigured: boolean
  /**
   * 后端**是否装得上本地 ASR**（faster-whisper + PyAV 可导入）。
   *
   * 这是依赖可用性探测，不是配置探测：线上 FC 镜像按 requirements-cloud.txt 构建，
   * 根本不含这两个包。前端据此决定要不要显示「本地 / 云端」选择器——线上访客
   * 既没有本地引擎可选，也不该被要求理解这个选择。
   *
   * 可选：旧后端不返回该字段，缺省按 false（不显示选择器）处理。
   */
  localAsrAvailable?: boolean
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

// ---- 错误契约 ----

/**
 * 错误码契约（以后端 `backend/app/errors.py` 为准）。
 *
 * 新增/删除错误码要同步四处：本文件、`src/lib/api-server.ts` 的 STATUS_BY_CODE、
 * `src/lib/error-titles.ts` 的中文标题表、`docs/api-contract.md`。
 */
export type ApiErrorCode =
  | 'INVALID_URL'
  | 'UNSUPPORTED_PLATFORM'
  | 'INVALID_TASK'
  | 'TRANSCRIBE_FAILED'
  | 'CLOUD_NOT_CONFIGURED'
  | 'SUMMARIZE_NOT_CONFIGURED'
  | 'TRANSCRIPT_TOO_LONG'
  | 'AUDIO_TOO_LARGE'
  | 'SUMMARIZE_FAILED'
  | 'TASK_INTERRUPTED'
  | 'TASK_TIMEOUT'
  | 'RATE_LIMITED'
  // 用户自带的 Key（BYOK）被服务商拒绝：401/403，见 backend/app/errors.py
  | 'BYOK_KEY_INVALID'
  // 用户自填的接口地址 / 模型名不合规（含 SSRF 拦截、填了地址没填模型）：400
  | 'BYOK_ENDPOINT_INVALID'
  | 'INTERNAL_ERROR'

export interface AppError {
  code: ApiErrorCode
  message: string
}

/** 所有非 2xx 响应的错误体形状 */
export interface ApiErrorBody {
  error: AppError
}
