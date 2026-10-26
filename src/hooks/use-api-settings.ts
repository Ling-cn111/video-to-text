'use client'

import { useEffect } from 'react'

/**
 * 「API 设置」面板的全局开关通道。
 *
 * 为什么不用 Context / 全局 store：需要打开它的地方分散在顶栏按钮、结果页总结卡、
 * 首页提示文案三处，而它们之间没有共同的客户端父组件边界可以放 Provider
 * （`src/app/layout.tsx` 是服务端组件，塞 Provider 会把整棵树拖成客户端）。
 * 一个 window 事件就够，且天然支持「任意位置触发、单一面板响应」。
 */
export const API_SETTINGS_OPEN_EVENT = 'api-settings:open'

/** 从任意位置打开设置面板（结果页「去填写 Key」这类入口用） */
export function openApiSettings(): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new Event(API_SETTINGS_OPEN_EVENT))
}

/** 订阅「请求打开设置面板」信号；handler 由面板自己传入 */
export function useApiSettingsOpenSignal(handler: () => void): void {
  useEffect(() => {
    window.addEventListener(API_SETTINGS_OPEN_EVENT, handler)
    return () => window.removeEventListener(API_SETTINGS_OPEN_EVENT, handler)
  }, [handler])
}
