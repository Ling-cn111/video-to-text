'use client'

import { useEffect } from 'react'
import { useTaskStore } from '@/stores/task-store'
import { useDocumentVisible, usePollInterval } from '@/hooks/use-poll-interval'

/**
 * 结果页轮询（P1-4）。
 *
 * 为什么不复用 `useTaskPolling`：那个 hook 在 `phase === 'done'` 时会
 * `router.replace(resultHref(taskId))` —— 它服务于「进度页 → 结果页」的跳转。
 * 结果页自己再挂一个同样的跳转就会反复 replace 自身（history 里堆满同一页）。
 *
 * 这里只做一件事：任务仍是 processing 时持续推进 phase，完成后自然停下。
 * 节奏与暂停策略与进度页一致（见 `use-poll-interval`）。
 */
export function useResultPolling(taskId: string | null) {
  const phase = useTaskStore((state) => state.phase)
  const stage = useTaskStore((state) => state.stage)
  const pollTask = useTaskStore((state) => state.pollTask)
  const visible = useDocumentVisible()
  const active = Boolean(taskId) && phase === 'transcribing' && visible
  const interval = usePollInterval(stage, active)

  useEffect(() => {
    if (!taskId || phase !== 'transcribing' || !visible) return
    void pollTask()
    const timer = window.setInterval(() => {
      void pollTask()
    }, interval)
    return () => window.clearInterval(timer)
  }, [interval, phase, pollTask, taskId, visible])
}