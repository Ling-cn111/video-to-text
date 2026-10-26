'use client'

import { useCallback, useEffect, useState } from 'react'

import { readAsrKey, readLlmKey } from '@/lib/byok'

/**
 * 用户是否已填了自己的 Key（BYOK）。
 *
 * 存在的理由：`capabilities.summarizeConfigured` / `cloudAsrConfigured` 只描述
 * **站点**有没有配 Key。BYOK 上线后「站点没配」不再等于「用不了」——用户自己
 * 填了 Key 就完全可用。若仍按 capabilities 一刀切禁用按钮，用户会看到一个
 * 灰掉的「生成 AI 总结」和「未配置」提示，而他刚在设置里填好了 Key。
 *
 * 因此所有能力门禁都要变成：`站点未配 && 用户未填` 才禁用。
 *
 * 实现细节：
 * - 初值 false，挂载后再读 localStorage（服务端没有 localStorage，直接读会让
 *   SSR 与 CSR 的 disabled 属性不一致）。
 * - 监听自定义事件 `byok:changed`：设置面板保存后派发，同页其他组件立刻重算，
 *   不用刷新。跨标签页的修改由 `storage` 事件兜住。
 */
export const BYOK_CHANGED_EVENT = 'byok:changed'

/** 保存 Key 后调用，通知同页所有订阅者重新计算 */
export function notifyByokChanged(): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new Event(BYOK_CHANGED_EVENT))
}

export interface ByokStatus {
  hasLlmKey: boolean
  hasAsrKey: boolean
  /** 已挂载并读过一次 localStorage；此前两个布尔都不可信 */
  ready: boolean
}

export function useByokStatus(): ByokStatus {
  const [status, setStatus] = useState<ByokStatus>({ hasLlmKey: false, hasAsrKey: false, ready: false })

  const refresh = useCallback(() => {
    setStatus({ hasLlmKey: Boolean(readLlmKey()), hasAsrKey: Boolean(readAsrKey()), ready: true })
  }, [])

  useEffect(() => {
    refresh()
    window.addEventListener(BYOK_CHANGED_EVENT, refresh)
    // storage 只在**其他**标签页改 localStorage 时触发，正好补上跨标签页这一路
    window.addEventListener('storage', refresh)
    return () => {
      window.removeEventListener(BYOK_CHANGED_EVENT, refresh)
      window.removeEventListener('storage', refresh)
    }
  }, [refresh])

  return status
}
