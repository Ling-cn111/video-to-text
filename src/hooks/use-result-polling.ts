'use client'

import { useEffect } from 'react'
import { useTaskStore } from '@/stores/task-store'

/** 与进度页保持同一节奏：800ms 一次，足够顺滑又不会打爆 API */
const POLL_INTERVAL_MS = 800

/**
 * 结果页轮询（P1-4）。
 *
 * 为什么不复用 `useTaskPolling`：那个 hook 在 `phase === 'done'` 时会
 * `router.replace(resultHref(taskId))` —— 它服务于「进度页 → 结果页」的跳转。
 * 结果页自己再挂一个同样的跳转就会反复 replace 自身（history 里堆满同一页）。
 *
 * 这里只做一件事：任务仍是 processing 时持续推进 phase，完成后自然停下。
 */
export function useResultPolling(taskId: string | null) {
  const phase = useTaskStore((state) => state.phase)
  const pollTask = useTaskStore((state) => state.pollTask)

  useEffect(() => {
    if (!taskId || phase !== 'transcribing') return
    void pollTask()
    const timer = setInterval(() => {
      void pollTask()
    }, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [phase, pollTask, taskId])
}
