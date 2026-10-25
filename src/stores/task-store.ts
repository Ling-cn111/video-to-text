import { create } from 'zustand'
import { api, ApiError, type ClientErrorCode } from '@/lib/api'
import { summarizeUnconfiguredMessage } from '@/lib/capability-hint'
import { ensureRecentTask, recordRecentTask, removeRecentTask, updateRecentTaskStatus } from '@/lib/recent-tasks'
import type { RecentTaskStatus } from '@/lib/recent-tasks'
import type {
  Capabilities,
  Summary,
  SummaryDetail,
  TaskStage,
  TranscribeEngine,
  TranscriptItem,
  TranscriptSource,
  VideoInfo,
} from '@/lib/types'

/** UI 层错误 = 接口契约错误码 + 客户端网络层错误码 */
export interface UiError {
  code: ClientErrorCode
  message: string
}

/**
 * 任务状态机：idle → parsing → transcribing → summarizing → done | error。
 * 进度页只消费 transcribing 之前的状态，结果页消费 done 状态；
 * 两个页面都支持按 taskId 从接口恢复（刷新不丢）。
 */
export type TaskPhase = 'idle' | 'parsing' | 'transcribing' | 'summarizing' | 'done' | 'error'

interface TaskState {
  phase: TaskPhase
  taskId: string | null
  video: VideoInfo | null
  /** 提交时的原始链接（真实转写后端需要下载音频） */
  sourceUrl: string | null
  stage: TaskStage | null
  progress: number
  transcript: TranscriptItem[] | null
  plainText: string | null
  summary: Summary | null
  /** 总结失败信息（P0 降级：不影响 phase，文字稿照常渲染，仅总结区显示错误与重试）。
   *  存整个 UiError 而不是字符串：总结区要按错误码区分展示（429 限流需要冷却提示），
   *  只留 message 会让「限流」和「服务异常」长得一模一样。 */
  summaryError: UiError | null
  error: UiError | null
  /** 转写引擎手动选择（首页设置；用户偏好，localStorage 持久化，跨任务保留，不随 reset 清空） */
  engine: TranscribeEngine
  /** 解析后自动 AI 总结（首页开关；用户偏好，localStorage 持久化，跨任务保留，不随 reset 清空） */
  autoSummary: boolean
  /** AI 总结详细度（用户偏好，localStorage 持久化，跨任务保留，不随 reset 清空） */
  summaryDetail: SummaryDetail
  /** 文字稿来源（字幕来源任务在结果页显示来源 Badge） */
  transcriptSource: TranscriptSource | null
  /** 能力探测（M2-P2-A）：null=检测中；会话内加载一次，跨任务保留；不随 reset 清空 */
  capabilities: Capabilities | null

  /** 首页提交链接：解析 + 创建转写任务。成功返回 taskId（供路由跳转），失败返回 null。 */
  submitUrl: (url: string) => Promise<string | null>
  /** 进度页轮询：拉取一次任务状态并推进状态机 */
  pollTask: () => Promise<void>
  /** 刷新 / 直接访问时按 taskId 恢复状态 */
  recover: (taskId: string) => Promise<void>
  /** 失败后重试：用已解析的视频重新创建任务 */
  retry: () => Promise<string | null>
  /** 总结失败后重试（文字稿已在手，直接重调 summarize）。
   *  可选传入 detail：结果页切换详细度时复用同一条路径（不走 autoSummary 判断，一定是用户显式要总结）。 */
  retrySummary: (detail?: SummaryDetail) => Promise<void>
  /** 加载能力探测（幂等；失败时乐观放行） */
  loadCapabilities: () => Promise<void>
  /** 首页切换转写模式 */
  setEngine: (engine: TranscribeEngine) => void
  /** 首页切换「解析后自动 AI 总结」（写入 localStorage） */
  setAutoSummary: (enabled: boolean) => void
  /** 结果页 / 首页切换总结详细度（写入 localStorage，并在结果页触发重新总结） */
  setSummaryDetail: (detail: SummaryDetail) => void
  /** 从 localStorage 水合用户偏好：挂载后调用（SSR 阶段不读 localStorage，避免水合不一致） */
  hydratePreferences: () => void
  reset: () => void
}

/** 本地偏好存储键：仅布尔值（'false' = 关闭），不含任何 Key 或隐私信息 */
const AUTO_SUMMARY_KEY = 'autoSummary'

/** 转写模式偏好键：'local' | 'cloud'，与 autoSummary 同一套水合路径 */
const ENGINE_KEY = 'engine'

/** 读偏好：读不到 / 存储不可用一律视为开启（默认开启） */
function readAutoSummary(): boolean {
  if (typeof window === 'undefined') return true
  try {
    return window.localStorage.getItem(AUTO_SUMMARY_KEY) !== 'false'
  } catch {
    return true
  }
}

function persistAutoSummary(enabled: boolean): void {
  try {
    window.localStorage.setItem(AUTO_SUMMARY_KEY, String(enabled))
  } catch {
    // 隐私模式 / 配额受限：仅本次会话生效，不阻塞主流程
  }
}

/** 读转写模式偏好：缺省 / 脏值一律回落 'local'（与锁定项「默认本地」一致） */
function readEngine(): TranscribeEngine {
  if (typeof window === 'undefined') return 'local'
  try {
    return window.localStorage.getItem(ENGINE_KEY) === 'cloud' ? 'cloud' : 'local'
  } catch {
    return 'local'
  }
}

function persistEngine(engine: TranscribeEngine): void {
  try {
    window.localStorage.setItem(ENGINE_KEY, engine)
  } catch {
    // 同 persistAutoSummary：存储不可用只影响跨会话记忆
  }
}

/** 总结详细度偏好键：'brief' | 'standard' | 'detailed' */
const DETAIL_KEY = 'summaryDetail'

/** 读详细度偏好：缺省 / 脏值一律回落 'standard'（与后端 DEFAULT_DETAIL 一致） */
function readSummaryDetail(): SummaryDetail {
  if (typeof window === 'undefined') return 'standard'
  try {
    const raw = window.localStorage.getItem(DETAIL_KEY)
    return raw === 'brief' || raw === 'detailed' ? raw : 'standard'
  } catch {
    return 'standard'
  }
}

function persistSummaryDetail(detail: SummaryDetail): void {
  try {
    window.localStorage.setItem(DETAIL_KEY, detail)
  } catch {
    // 同 persistAutoSummary：存储不可用只影响跨会话记忆
  }
}

/**
 * 取当前 hostname 用于选文案（本地给配置指引，公网只说「暂不可用」）。
 * store 里的提示在事件回调里生成，此时一定在浏览器环境；SSR 兜底返回空串，
 * 空串按「非本机」处理，也就是宁可少说内部细节。
 */
function currentHostname(): string {
  if (typeof window === 'undefined') return ''
  return window.location.hostname
}

/**
 * 总结结果缓存（sessionStorage，键 `summary:<taskId>:<detail>`）。
 *
 * 为什么需要：`POST /api/summarize` 是无状态接口，后端不落库。刷新结果页会重新
 * 走一遍 `recover()`，把整份文字稿再传一次、再调一次 LLM —— 每次刷新都在重复计费。
 * 缓存命中后直接复用上次的结构化结果，不再发请求。
 *
 * 为什么键里带 detail：同一份文字稿在「精简 / 标准 / 详细」下是三次不同的调用，
 * 只按 taskId 缓存会让用户切到「详细」却拿回「精简」的旧结果 —— 缓存反而成了 bug。
 *
 * 为什么是 sessionStorage 而不是 localStorage：总结只在当前标签页的生命周期内有意义，
 * 关标签页即失效，避免 7 天任务 TTL 内留下过期的陈旧总结。
 */
const SUMMARY_CACHE_PREFIX = 'summary:'
/** 缓存条目上限：超出按插入顺序淘汰最旧的（总结单条约 1-2KB，20 条远低于配额） */
const SUMMARY_CACHE_LIMIT = 20

function summaryCacheKey(taskId: string, detail: SummaryDetail): string {
  return `${SUMMARY_CACHE_PREFIX}${taskId}:${detail}`
}

/** 读缓存：形状不对（旧版本 / 手工篡改）一律当作未命中，宁可重算也不渲染脏数据 */
function readCachedSummary(taskId: string, detail: SummaryDetail): Summary | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.sessionStorage.getItem(summaryCacheKey(taskId, detail))
    if (!raw) return null
    const parsed = JSON.parse(raw) as Summary
    if (!parsed || typeof parsed.summary !== 'string' || !Array.isArray(parsed.keyPoints)) return null
    return parsed
  } catch {
    return null
  }
}

function persistSummary(taskId: string, detail: SummaryDetail, summary: Summary): void {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(summaryCacheKey(taskId, detail), JSON.stringify(summary))
    pruneSummaryCache()
  } catch {
    // 配额受限 / 隐私模式：缓存失效不影响主流程，只是下次仍会重新请求
  }
}

function pruneSummaryCache(): void {
  const keys: string[] = []
  for (let index = 0; index < window.sessionStorage.length; index += 1) {
    const key = window.sessionStorage.key(index)
    if (key?.startsWith(SUMMARY_CACHE_PREFIX)) keys.push(key)
  }
  if (keys.length <= SUMMARY_CACHE_LIMIT) return
  for (const key of keys.slice(0, keys.length - SUMMARY_CACHE_LIMIT)) {
    window.sessionStorage.removeItem(key)
  }
}

/** 防止轮询重入 */
let pollInFlight = false

const IDLE = {
  phase: 'idle' as const,
  taskId: null,
  video: null,
  sourceUrl: null,
  stage: null,
  progress: 0,
  transcript: null,
  plainText: null,
  summary: null,
  summaryError: null,
  error: null,
  transcriptSource: null,
}

function toAppError(error: unknown): UiError {
  if (error instanceof ApiError) {
    return { code: error.code, message: error.message }
  }
  return { code: 'INTERNAL_ERROR', message: '出了点问题，请稍后重试' }
}

/**
 * 任务完成但没有可用文字稿（P1-5）。
 *
 * `transcript: []` 是 truthy —— 旧代码用 `!task.transcript` 判断，空数组会被当成
 * 「有稿」，结果页渲染出「共 0 段」的空壳，用户完全不知道发生了什么。
 * 后端对空识别结果会抛 TRANSCRIBE_FAILED，所以走到这里的空数组多半来自历史数据
 * 或异常路径，统一按「没识别出内容」引导重试。
 */
function isEmptyTranscript(transcript: TranscriptItem[] | null | undefined): boolean {
  return !transcript || transcript.length === 0
}

/** 完成态但文字稿为空时的统一错误（区分「任务不存在」与「识别结果为空」） */
function emptyTranscriptError(task: { transcript?: TranscriptItem[] | null }): UiError {
  if (task.transcript === null || task.transcript === undefined) {
    return { code: 'INVALID_TASK', message: '任务不存在或已过期，请重新解析视频链接' }
  }
  return { code: 'TRANSCRIBE_FAILED', message: '未能识别出语音内容，请换一个视频或确认原视频有清晰人声' }
}

/**
 * 记录到首页「最近转写」（P1-7）。
 *
 * 提交成功就先记一条 processing：这是「可以关页面了」这个承诺的兑现方式 ——
 * 没有记录时，用户一关标签页就彻底失联（taskId 只在 URL 里）。
 * 转写失败时把这条删掉（见 forgetTask），别在首页留一堆点进去就报错的死链。
 * 重复调用按 taskId 去重（见 recent-tasks.ts）。
 */
function rememberTask(taskId: string, video: VideoInfo | null | undefined, status: RecentTaskStatus): void {
  if (!video) return
  recordRecentTask({
    taskId,
    title: video.title,
    cover: video.cover,
    duration: video.duration,
    platform: video.platform,
    status,
  })
}

/**
 * 补记一条（仅当不存在）。用于刷新 / 直链恢复：
 * 那里每次都无条件 record 会把 savedAt 刷成「刚刚」，首页的相对时间就失效了。
 */
function ensureRemembered(taskId: string, video: VideoInfo | null | undefined): void {
  if (!video) return
  ensureRecentTask({
    taskId,
    title: video.title,
    cover: video.cover,
    duration: video.duration,
    platform: video.platform,
    status: 'done',
  })
}

/** 失败任务从「最近转写」里摘掉：留在首页只会变成死链 */
function forgetTask(taskId: string): void {
  removeRecentTask(taskId)
}

export const useTaskStore = create<TaskState>((set, get) => ({
  ...IDLE,
  engine: 'local',
  autoSummary: true,
  summaryDetail: 'standard',
  capabilities: null,

  loadCapabilities: async () => {
    if (get().capabilities) return // 幂等：会话内只探测一次
    try {
      const capabilities = await api.getCapabilities()
      set({ capabilities })
    } catch {
      // 探测失败不阻塞主流程：乐观放行（后端不可达时后续请求会给出明确错误）
      set({ capabilities: { cloudAsrConfigured: true, summarizeConfigured: true } })
    }
  },

  submitUrl: async (url) => {
    set({ ...IDLE, phase: 'parsing' })
    try {
      const video = await api.parseVideo(url)
      set({ video, sourceUrl: url })
      const task = await api.startTranscription(video.videoId, video, url, get().engine)
      set({ taskId: task.taskId, stage: task.stage, progress: task.progress, phase: 'transcribing' })
      // P1-7：任务一创建就落一条记录，用户此刻关掉页面也还能从首页找回
      rememberTask(task.taskId, video, 'processing')
      return task.taskId
    } catch (error) {
      set({ phase: 'error', error: toAppError(error) })
      return null
    }
  },

  pollTask: async () => {
    const { taskId, phase } = get()
    if (!taskId || phase !== 'transcribing' || pollInFlight) return
    pollInFlight = true
    try {
      const task = await api.getTask(taskId)
      if (get().taskId !== taskId) return

      set({ stage: task.stage, progress: task.progress, video: task.video ?? get().video })

      if (task.status === 'interrupted') {
        // 服务重启后未完成的历史任务（SQLite 持久化恢复）：引导重试
        forgetTask(taskId)
        set({ phase: 'error', error: { code: 'TASK_INTERRUPTED', message: '任务已中断（服务重启过），请重试' } })
        return
      }
      if (task.status === 'failed') {
        // 失败任务从「最近转写」摘掉：留着就是一条点进去必报错的死链
        forgetTask(taskId)
        set({ phase: 'error', error: task.error ?? { code: 'TRANSCRIBE_FAILED', message: '转写失败，请重试' } })
        return
      }
      if (task.status === 'completed') {
        // P1-5：空数组也要按「没稿」处理，不能让结果页渲染 0 段空壳
        if (isEmptyTranscript(task.transcript)) {
          forgetTask(taskId)
          set({ phase: 'error', error: emptyTranscriptError(task) })
          return
        }
        const transcriptReady = {
          transcript: task.transcript ?? null,
          plainText: task.plainText ?? null,
          transcriptSource: task.transcriptSource ?? null,
        }
        // P1-7：确认有稿才把记录标成 done（首页据此决定是「继续看」还是「看结果」）。
        // 有 video 就整条重记（顺带刷新 savedAt 排到最前），没有则只改状态位。
        const doneVideo = task.video ?? get().video
        if (doneVideo) {
          rememberTask(taskId, doneVideo, 'done')
        } else {
          updateRecentTaskStatus(taskId, 'done')
        }
        // 用户偏好（autoSummary=false）：转写照常完成，不自动调用 summarize，结果页提供手动生成入口
        if (!get().autoSummary) {
          set({ phase: 'done', summary: null, summaryError: null, ...transcriptReady })
          return
        }
        set({ phase: 'summarizing', summaryError: null, ...transcriptReady })
        // 能力前置：未配置 LLM Key 时不发起无用请求，直接给出引导（M2-P2-A）
        if (get().capabilities?.summarizeConfigured === false) {
          set({ phase: 'done', summary: null, summaryError: { code: 'SUMMARIZE_NOT_CONFIGURED', message: summarizeUnconfiguredMessage(currentHostname()) } })
          return
        }
        try {
          const detail = get().summaryDetail
          const summary = await api.summarize({
            taskId,
            transcript: task.transcript ?? [],
            title: (task.video ?? get().video)?.title ?? '',
            duration: (task.video ?? get().video)?.duration ?? 0,
            detail,
          })
          if (get().taskId !== taskId) return
          persistSummary(taskId, detail, summary)
          set({ phase: 'done', summary })
        } catch (error) {
          // P0 降级：总结失败不改变任务 phase，文字稿照常渲染，仅记录总结错误
          if (get().taskId !== taskId) return
          set({ phase: 'done', summary: null, summaryError: toAppError(error) })
        }
      }
    } catch (error) {
      set({ phase: 'error', error: toAppError(error) })
    } finally {
      pollInFlight = false
    }
  },

  recover: async (taskId) => {
    if (get().taskId === taskId && get().phase === 'done') return
    set({ ...IDLE, phase: 'parsing' })
    try {
      const task = await api.getTask(taskId)
      set({ taskId, video: task.video ?? null, stage: task.stage, progress: task.progress })

      if (task.status === 'interrupted') {
        forgetTask(taskId)
        set({ phase: 'error', error: { code: 'TASK_INTERRUPTED', message: '任务已中断（服务重启过），请重试' } })
        return
      }
      if (task.status === 'failed') {
        forgetTask(taskId)
        set({ phase: 'error', error: task.error ?? { code: 'TRANSCRIBE_FAILED', message: '转写失败，请重试' } })
        return
      }
      if (task.status === 'processing') {
        // 直接访问结果页但任务还在跑：置为 transcribing，由结果页的 useResultPolling
        // 继续推进（P1-4：旧实现只 set phase 就 return，没有任何东西会再拉状态，
        // 结果页于是永远停在骨架屏）。
        set({ phase: 'transcribing' })
        return
      }
      // P1-5：`transcript: []` 是 truthy，`!task.transcript` 会把空数组判成有稿
      if (isEmptyTranscript(task.transcript) || !task.transcript) {
        forgetTask(taskId)
        set({ phase: 'error', error: emptyTranscriptError(task) })
        return
      }
      // 到这里 transcript 一定是非空数组
      const readyTranscript = task.transcript
      // P1-7：直链 / 刷新恢复的完成态补记一次（仅当不存在，不刷新 savedAt）
      ensureRemembered(taskId, task.video ?? get().video)
      // 用户偏好（autoSummary=false）：刷新 / 直链恢复同样尊重开关，不自动调用 summarize
      if (!get().autoSummary) {
        set({
          phase: 'done',
          summary: null,
          summaryError: null,
          transcript: readyTranscript,
          plainText: task.plainText ?? null,
          transcriptSource: task.transcriptSource ?? null,
        })
        return
      }
      set({ phase: 'summarizing', transcript: readyTranscript, plainText: task.plainText ?? null, transcriptSource: task.transcriptSource ?? null, summaryError: null })
      // 能力前置（M2-P2-A）：未配置 LLM Key 时不发起无用请求
      if (get().capabilities?.summarizeConfigured === false) {
        set({ phase: 'done', summary: null, summaryError: { code: 'SUMMARIZE_NOT_CONFIGURED', message: summarizeUnconfiguredMessage(currentHostname()) } })
        return
      }
      // 缓存命中：刷新 / 直链恢复不再重传整份文字稿、不再重复调 LLM（后端 summarize 无状态、不落库）
      const detail = get().summaryDetail
      const cached = readCachedSummary(taskId, detail)
      if (cached) {
        set({ phase: 'done', summary: cached })
        return
      }
      try {
        const summary = await api.summarize({
          taskId,
          transcript: readyTranscript,
          title: (task.video ?? get().video)?.title ?? '',
          duration: (task.video ?? get().video)?.duration ?? 0,
          detail,
        })
        persistSummary(taskId, detail, summary)
        set({ phase: 'done', summary })
      } catch (error) {
        // P0 降级：同 pollTask —— 文字稿可渲染，仅总结区降级
        set({ phase: 'done', summary: null, summaryError: toAppError(error) })
      }
    } catch (error) {
      set({ phase: 'error', error: toAppError(error) })
    }
  },

  retry: async () => {
    const { video, sourceUrl } = get()
    if (!video) return null
    set({ phase: 'parsing', error: null, stage: null, progress: 0 })
    try {
      // 重试沿用提交时的转写模式（engine 为用户偏好，跨任务保留）
      const task = await api.startTranscription(video.videoId, video, sourceUrl ?? undefined, get().engine)
      set({ taskId: task.taskId, stage: task.stage, progress: task.progress, phase: 'transcribing' })
      return task.taskId
    } catch (error) {
      set({ phase: 'error', error: toAppError(error) })
      return null
    }
  },

  retrySummary: async (detail) => {
    const { taskId, transcript, video } = get()
    if (!taskId || !transcript) return
    // 显式传入的档位优先（结果页切换详细度走这条路径），否则用当前偏好
    const target = detail ?? get().summaryDetail
    if (detail) set({ summaryDetail: detail })
    set({ summaryError: null, summary: null })
    try {
      // 切档位前先看缓存：来回切换时不该每次都付一次 LLM 的钱
      const cached = readCachedSummary(taskId, target)
      if (cached) {
        set({ summary: cached })
        return
      }
      const summary = await api.summarize({
        taskId,
        transcript,
        title: video?.title ?? '',
        duration: video?.duration ?? 0,
        detail: target,
      })
      // 手动重试 / 换档位成功同样写缓存：此后刷新不再重复计费
      persistSummary(taskId, target, summary)
      set({ summary })
    } catch (error) {
      set({ summaryError: toAppError(error) })
    }
  },

  setEngine: (engine) => {
    set({ engine })
    persistEngine(engine)
  },

  setAutoSummary: (enabled) => {
    set({ autoSummary: enabled })
    persistAutoSummary(enabled)
  },

  setSummaryDetail: (detail) => {
    set({ summaryDetail: detail })
    persistSummaryDetail(detail)
  },

  hydratePreferences: () =>
    set({ autoSummary: readAutoSummary(), engine: readEngine(), summaryDetail: readSummaryDetail() }),

  // 不清 engine / autoSummary / summaryDetail：均为用户偏好，跨任务保留
  reset: () => set({ ...IDLE }),
}))

/** 非组件场景读取状态（如导出前校验） */
export const getTaskState = useTaskStore.getState
