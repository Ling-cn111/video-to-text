'use client'

import { useEffect } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { IS_MOBILE_SHELL } from '@/lib/runtime'

/**
 * Android 物理返回键（移动端壳内启用）。
 *
 * 为什么必须有：Capacitor 默认行为是「按下返回键 → 退出 App」。用户在结果页
 * 按返回，期望回到首页，实际整个 App 关掉 —— 这是 Android 上最刺眼的体验缺陷。
 *
 * 处理策略（从内到外，逐级退出）：
 * 1. 当前页不是首页 → 调 `router.back()`，让应用内历史正常回退
 * 2. 已在首页 → 把控制权交还系统（不调用 `exitApp`），保留用户「再按一次退出」
 *    的直觉；强行 exitApp 会让用户误以为应用崩溃
 *
 * 为什么用 `history.state.idx` 判断能否回退：Next App Router 会把内部历史索引写在
 * `history.state.idx`。idx > 0 说明应用内有上一页；idx === 0 说明这是会话第一页，
 * 再 back 就会离开应用（Android 上表现为退出）。
 *
 * 只在 `IS_MOBILE_SHELL` 下注册：网页与桌面端没有物理返回键，`@capacitor/app`
 * 的动态 import 也会被 webpack 排除在初始包里。
 */
export function useAndroidBack(): void {
  const router = useRouter()
  const pathname = usePathname()

  useEffect(() => {
    if (!IS_MOBILE_SHELL) return

    let remove: (() => void) | undefined
    let disposed = false

    void (async () => {
      try {
        const { App } = await import('@capacitor/app')
        const handle = await App.addListener('backButton', () => {
          const idx = (window.history.state as { idx?: number } | null)?.idx ?? 0
          if (idx > 0) {
            router.back()
            return
          }
          // 已在会话第一页：交给系统处理（再次按下即退出），不主动 exitApp
        })
        if (disposed) {
          void handle.remove()
          return
        }
        remove = () => void handle.remove()
      } catch (error) {
        // 非 Capacitor 环境（例如浏览器里跑 mobile 构建做冒烟测试）会抛错，忽略即可
        console.warn('[mobile] 返回键监听未注册', error)
      }
    })()

    return () => {
      disposed = true
      remove?.()
    }
  }, [router, pathname])
}
