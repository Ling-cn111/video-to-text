import { create } from 'zustand'
import { api, ApiError, type ClientErrorCode } from '@/lib/api'
import type {
  Summary,
  TaskStage,
  TranscriptItem,
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
  error: UiError | null

  /** 首页提交链接：解析 + 创建转写任务。成功返回 taskId（供路由跳转），失败返回 null。 */
  submitUrl: (url: string) => Promise<string | null>
  /** 进度页轮询：拉取一次任务状态并推进状态机 */
  pollTask: () => Promise<void>
  /** 刷新 / 直接访问时按 taskId 恢复状态 */
  recover: (taskId: string) => Promise<void>
  /** 失败后重试：用已解析的视频重新创建任务 */
  retry: () => Promise<string | null>
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
  error: null,
}

function toAppError(error: unknown): UiError {
  if (error instanceof ApiError) {
    return { code: error.code, message: error.message }
  }
  return { code: 'INTERNAL_ERROR', message: '出了点问题，请稍后重试' }
}

export const useTaskStore = create<TaskState>((set, get) => ({
  ...IDLE,

  submitUrl: async (url) => {
    set({ ...IDLE, phase: 'parsing' })
    try {
      const video = await api.parseVideo(url)
      set({ video, sourceUrl: url })
      const task = await api.startTranscription(video.videoId, video, url)
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

      if (task.status === 'failed') {
        set({ phase: 'error', error: task.error ?? { code: 'TRANSCRIBE_FAILED', message: '转写失败，请重试' } })
        return
      }
      if (task.status === 'completed') {
        set({ phase: 'summarizing', transcript: task.transcript ?? null, plainText: task.plainText ?? null })
        const summary = await api.summarize(taskId)
        if (get().taskId !== taskId) return
        set({ phase: 'done', summary })
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
      set({ phase: 'summarizing', transcript: task.transcript, plainText: task.plainText ?? null })
      const summary = await api.summarize(taskId)
      set({ phase: 'done', summary })
    } catch (error) {
      set({ phase: 'error', error: toAppError(error) })
    }
  },

  retry: async () => {
    const { video, sourceUrl } = get()
    if (!video) return null
    set({ phase: 'parsing', error: null, stage: null, progress: 0 })
    try {
      const task = await api.startTranscription(video.videoId, video, sourceUrl ?? undefined)
      set({ taskId: task.taskId, stage: task.stage, progress: task.progress, phase: 'transcribing' })
      return task.taskId
    } catch (error) {
      set({ phase: 'error', error: toAppError(error) })
      return null
    }
  },

  reset: () => set({ ...IDLE }),
}))

/** 非组件场景读取状态（如导出前校验） */
export const getTaskState = useTaskStore.getState
