'use client'

import { useEffect } from 'react'

/**
 * 离开保护（P1-8）。
 *
 * 场景：转写已经跑了 3 分钟，用户手滑关掉标签页或点了刷新 —— 任务在后端还在跑，
 * 但用户失去了这个 taskId 的唯一入口（此前 taskId 只活在 URL 里）。
 *
 * 为什么只拦「关闭 / 刷新」而不拦应用内跳转：应用内跳转由
 * `processing-view` 的「不等了，先回首页」按钮负责（它是有意的操作，
 * 再弹一个确认框就是二次打扰）。`beforeunload` 只处理浏览器级离开。
 *
 * 注意浏览器限制：现代浏览器不允许自定义提示文案，只能触发原生确认框，
 * 且必须由用户交互过页面才会生效。所以这里的文案只是给老浏览器兜底。
 */
export function useLeaveGuard(active: boolean): void {
  useEffect(() => {
    if (!active) return

    const handler = (event: BeforeUnloadEvent) => {
      // 转写还在跑：让浏览器弹原生确认框
      event.preventDefault()
      // 老浏览器（Chrome < 119 / 部分 Safari）仍读 returnValue
      event.returnValue = '转写还在进行中，离开后任务仍会继续，但需要从首页「最近转写」找回。'
      return event.returnValue
    }

    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [active])
}
