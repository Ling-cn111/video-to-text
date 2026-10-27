'use client'

import { useCallback, useEffect, useState } from 'react'

import { llmConfigIssue, readLlmConfig, type LlmConfig } from '@/lib/byok'

/**
 * 用户是否已配好自己的 LLM（BYOK：Key + 可选的接口地址与模型）。
 *
 * 存在的理由：`capabilities.summarizeConfigured` 只描述**站点**有没有配 Key。
 * BYOK 上线后「站点没配」不再等于「用不了」——用户自己填了 Key 就完全可用。
 * 若仍按 capabilities 一刀切禁用按钮，用户会看到一个灰掉的「生成 AI 总结」
 * 和「未配置」提示，而他刚在设置里填好了 Key。
 *
 * 因此总结相关的能力门禁是：`站点未配 && 用户未填` 才禁用。
 *
 * 实现细节：
 * - 初值 false，挂载后再读 localStorage（服务端没有 localStorage，直接读会让
 *   SSR 与 CSR 的 disabled 属性不一致）。
 * - 监听自定义事件 `byok:changed`：设置面板保存后派发，同页其他组件立刻重算，
 *   不用刷新。跨标签页的修改由 `storage` 事件兜住。
 */
export const BYOK_CHANGED_EVENT = 'byok:changed'

/** 保存配置后调用，通知同页所有订阅者重新计算 */
export function notifyByokChanged(): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new Event(BYOK_CHANGED_EVENT))
}

export interface ByokStatus {
  /** 是否填了自己的 Key（只看 Key，不看地址与模型） */
  hasLlmKey: boolean
  /** 已挂载并读过一次 localStorage；此前布尔不可信 */
  ready: boolean
  /** 当前完整配置（含地址与模型），面板与结果页提示用 */
  config: LlmConfig
  /**
   * 配置本身不自洽时的人话说明（如「填了地址却没填模型」）。
   *
   * 为什么要在门禁层暴露它：这种配置发出去必然失败，但按钮不该直接灰掉——
   * 用户需要看到「哪里不对」才知道怎么改。调用方据此在引导态里显示这句提示。
   */
  configIssue: string | null
}

const INITIAL: ByokStatus = {
  hasLlmKey: false,
  ready: false,
  config: { key: null, baseUrl: null, model: null },
  configIssue: null,
}

export function useByokStatus(): ByokStatus {
  const [status, setStatus] = useState<ByokStatus>(INITIAL)

  const refresh = useCallback(() => {
    const config = readLlmConfig()
    setStatus({
      hasLlmKey: Boolean(config.key),
      ready: true,
      config,
      configIssue: llmConfigIssue(config),
    })
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
