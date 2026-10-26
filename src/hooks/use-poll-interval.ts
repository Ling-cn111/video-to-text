'use client'

import { useEffect, useRef, useState } from 'react'
import { pollIntervalFor, msUntilSlow } from '@/lib/poll-rhythm'

/**
 * 轮询节奏钩子：把 `poll-rhythm` 的纯函数接到组件生命周期上。
 *
 * 为什么按「阶段已持续时长」而不是「任务总时长」退避：
 * 阶段切换才是用户真正在等的信号，任务刚开始时不能慢（否则像没反应），
 * 卡在 asr 阶段几分钟时也没必要一直 800ms 打。
 */

interface BackoffState {
  /** 进入当前阶段的时刻（毫秒） */
  stageSince: number
  stage: string | null
}

/** 返回当前应使用的轮询间隔；`active` 为 false 时返回 0（调用方据此不启动定时器） */
export function usePollInterval(stage: string | null, active: boolean): number {
  const stateRef = useRef<BackoffState>({ stageSince: Date.now(), stage: null })
  // 注意：不要把这个 setter 命名成 setInterval —— 会遮蔽全局定时器，之后想加定时器时极其隐蔽
  const [current, setCurrent] = useState(pollIntervalFor(0))

  useEffect(() => {
    if (!active) return
    const now = Date.now()
    const state = stateRef.current
    // 阶段变化：重置快档窗口（用户此刻最想看到进度动起来）
    if (state.stage !== stage) {
      stateRef.current = { stageSince: now, stage }
      setCurrent(pollIntervalFor(0))
      return
    }

    const elapsed = now - state.stageSince
    setCurrent(pollIntervalFor(elapsed))

    // 到点自动切慢档：否则没有新进度时定时器不会重算
    const wait = msUntilSlow(elapsed)
    if (wait > 0) {
      const timer = window.setTimeout(() => setCurrent(pollIntervalFor(0)), wait)
      return () => window.clearTimeout(timer)
    }
  }, [active, stage])

  return active ? current : 0
}

/** 标签页是否可见（后台时暂停轮询：浏览器本来就会节流定时器，挂着只是浪费） */
export function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(true)

  useEffect(() => {
    const sync = () => setVisible(document.visibilityState === 'visible')
    sync()
    document.addEventListener('visibilitychange', sync)
    return () => document.removeEventListener('visibilitychange', sync)
  }, [])

  return visible
}
