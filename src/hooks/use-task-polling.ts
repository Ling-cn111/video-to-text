'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useTaskStore } from '@/stores/task-store'

const POLL_INTERVAL_MS = 800

/**
 * 任务轮询：transcribing 阶段每 800ms 拉取一次任务状态；
 * 进入 done 后自动跳转结果页。
 */
export function useTaskPolling(taskId: string) {
  const router = useRouter()
  const phase = useTaskStore((state) => state.phase)
  const pollTask = useTaskStore((state) => state.pollTask)

  useEffect(() => {
    if (phase !== 'transcribing') return
    void pollTask()
    const timer = setInterval(() => {
      void pollTask()
    }, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [phase, pollTask])

  useEffect(() => {
    if (phase === 'done') {
      router.replace(`/result/${taskId}`)
    }
  }, [phase, taskId, router])
}
