'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useTaskStore } from '@/stores/task-store'
import { usePollInterval, useDocumentVisible } from '@/hooks/use-poll-interval'
import { resultHref } from '@/lib/routes'

/**
 * 任务轮询：transcribing 阶段按退避节奏拉取任务状态（P1-8）；
 * 进入 done 后自动跳转结果页。
 *
 * 节奏与暂停逻辑见 `use-poll-interval`：前 10 秒 800ms 保证能看到阶段跳变，
 * 之后退到 3 秒；标签页在后台时完全暂停，切回来立刻补一次。
 */
export function useTaskPolling(taskId: string) {
  const router = useRouter()
  const phase = useTaskStore((state) => state.phase)
  const stage = useTaskStore((state) => state.stage)
  const pollTask = useTaskStore((state) => state.pollTask)
  const visible = useDocumentVisible()
  const active = phase === 'transcribing' && visible
  const interval = usePollInterval(stage, active)

  useEffect(() => {
    if (phase !== 'transcribing' || !visible) return
    // 挂载 / 切回前台时立刻补一次：不能等到第一个 interval 到点，
    // 否则用户从后台切回来会先看到几秒的旧进度。
    void pollTask()
    const timer = window.setInterval(() => {
      void pollTask()
    }, interval)
    return () => window.clearInterval(timer)
  }, [interval, phase, pollTask, visible])

  useEffect(() => {
    if (phase === 'done') {
      router.replace(resultHref(taskId))
    }
  }, [phase, taskId, router])
}
