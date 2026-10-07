import { create } from 'zustand'
import { api, ApiError, type ClientErrorCode } from '@/lib/api'
import type {
  Summary,
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
  /** 总结失败信息（P0 降级：不影响 phase，文字稿照常渲染，仅总结区显示错误与重试） */
  summaryError: string | null
  error: UiError | null
  /** 转写引擎手动选择（首页设置；用户偏好，跨任务保留，不随 reset 清空） */
  engine: TranscribeEngine
  /** 文字稿来源（字幕来源任务在结果页显示来源 Badge） */
  transcriptSource: TranscriptSource | null

  /** 首页提交链接：解析 + 创建转写任务。成功返回 taskId（供路由跳转），失败返回 null。 */
  submitUrl: (url: string) => Promise<string | null>
  /** 进度页轮询：拉取一次任务状态并推进状态机 */
  pollTask: () => Promise<void>
  /** 刷新 / 直接访问时按 taskId 恢复状态 */
  recover: (taskId: string) => Promise<void>
  /** 失败后重试：用已解析的视频重新创建任务 */
  retry: () => Promise<string | null>
  /** 总结失败后重试（文字稿已在手，直接重调 summarize） */
  retrySummary: () => Promise<void>
  /** 首页切换转写模式 */
  setEngine: (engine: TranscribeEngine) => void
  reset: () => void
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

export const useTaskStore = create<TaskState>((set, get) => ({
  ...IDLE,
  engine: 'local',

  submitUrl: async (url) => {
    set({ ...IDLE, phase: 'parsing' })
    try {
      const video = await api.parseVideo(url)
      set({ video, sourceUrl: url })
      const task = await api.startTranscription(video.videoId, video, url, get().engine)
      set({ taskId: task.taskId, stage: task.stage, progress: task.progress, phase: 'transcribing' })
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
        set({ phase: 'error', error: { code: 'TASK_INTERRUPTED', message: '任务已中断（服务重启过），请重试' } })
        return
      }
      if (task.status === 'failed') {
        set({ phase: 'error', error: task.error ?? { code: 'TRANSCRIBE_FAILED', message: '转写失败，请重试' } })
        return
      }
      if (task.status === 'completed') {
        set({
          phase: 'summarizing',
          transcript: task.transcript ?? null,
          plainText: task.plainText ?? null,
          transcriptSource: task.transcriptSource ?? null,
          summaryError: null,
        })
        try {
          const summary = await api.summarize({
            taskId,
            transcript: task.transcript ?? [],
            title: (task.video ?? get().video)?.title ?? '',
            duration: (task.video ?? get().video)?.duration ?? 0,
          })
          if (get().taskId !== taskId) return
          set({ phase: 'done', summary })
        } catch (error) {
          // P0 降级：总结失败不改变任务 phase，文字稿照常渲染，仅记录总结错误
          if (get().taskId !== taskId) return
          set({ phase: 'done', summary: null, summaryError: toAppError(error).message })
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
        set({ phase: 'error', error: { code: 'TASK_INTERRUPTED', message: '任务已中断（服务重启过），请重试' } })
        return
      }
      if (task.status === 'failed') {
        set({ phase: 'error', error: task.error ?? { code: 'TRANSCRIBE_FAILED', message: '转写失败，请重试' } })
        return
      }
      if (task.status === 'processing') {
        // 直接访问进度页但任务未完成：交给轮询继续推进
        set({ phase: 'transcribing' })
        return
      }
      if (!task.transcript) {
        set({ phase: 'error', error: { code: 'INVALID_TASK', message: '任务不存在或已过期，请重新解析视频链接' } })
        return
      }
      set({ phase: 'summarizing', transcript: task.transcript, plainText: task.plainText ?? null, transcriptSource: task.transcriptSource ?? null, summaryError: null })
      try {
        const summary = await api.summarize({
          taskId,
          transcript: task.transcript,
          title: (task.video ?? get().video)?.title ?? '',
          duration: (task.video ?? get().video)?.duration ?? 0,
        })
        set({ phase: 'done', summary })
      } catch (error) {
        // P0 降级：同 pollTask —— 文字稿可渲染，仅总结区降级
        set({ phase: 'done', summary: null, summaryError: toAppError(error).message })
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

  retrySummary: async () => {
    const { taskId, transcript, video } = get()
    if (!taskId || !transcript) return
    set({ summaryError: null })
    try {
      const summary = await api.summarize({
        taskId,
        transcript,
        title: video?.title ?? '',
        duration: video?.duration ?? 0,
      })
      set({ summary })
    } catch (error) {
      set({ summaryError: toAppError(error).message })
    }
  },

  setEngine: (engine) => set({ engine }),

  reset: () => set({ ...IDLE }),  // 不清 engine：模式选择是用户偏好，跨任务保留
}))

/** 非组件场景读取状态（如导出前校验） */
export const getTaskState = useTaskStore.getState
