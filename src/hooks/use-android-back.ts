'use client'

import { useEffect, useRef } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { toast } from 'sonner'
import { IS_MOBILE_SHELL } from '@/lib/runtime'
import { resolveBackAction } from '@/lib/back-navigation'

/**
 * Android 返回键（移动端壳内启用）。
 *
 * 为什么必须有：Capacitor 默认行为是「按下返回键 → 退出 App」。用户在结果页
 * 按返回，期望回到首页，实际整个 App 关掉 —— 这是 Android 上最刺眼的体验缺陷。
 *
 * 处理策略见 `src/lib/back-navigation.ts` 的 `resolveBackAction`：
 * 非首页回退 / 非首页但无上一页时回首页 / 首页二次确认后退出。
 *
 * ## 为什么用 `pathname` 而不是 `history.state.idx`
 *
 * 第一版用 `history.state.idx > 0` 判断能否回退，那个字段是 **Pages Router** 的，
 * App Router 根本不写（只有 `__NA` 与 `__PRIVATE_NEXTJS_INTERNALS_TREE`）。
 * 结果 `idx` 恒为 0，`router.back()` 一次都没执行 —— 真机上就是「按返回没反应」。
 * 详见 `src/lib/back-navigation.ts` 的注释。
 *
 * ## 为什么 pathname 走 ref 而不是 effect 依赖
 *
 * 依赖 `[pathname]` 会在每次跳转时先 remove 再重新 addListener，中间存在一段
 * 监听器真空期；用户在跳转动画刚结束时按返回会掉进 Capacitor 的默认分支。
 * 改成只注册一次、用 ref 读当前路径，监听器全程在线。
 *
 * 只在 `IS_MOBILE_SHELL` 下注册：网页与桌面端没有物理返回键，`@capacitor/app`
 * 的动态 import 也会被 webpack 排除在初始包里。
 */

/** 「再按一次退出」的确认窗口。Android 惯例是 2 秒左右。 */
const EXIT_CONFIRM_MS = 2000

export function useAndroidBack(): void {
  const router = useRouter()
  const pathname = usePathname()
  const pathnameRef = useRef(pathname)
  const exitArmedAt = useRef(0)

  // 单独同步 ref：不参与下方 effect 的依赖，监听器不会被重建
  useEffect(() => {
    pathnameRef.current = pathname
    // 路径一变就撤销「再按一次退出」的待确认状态：
    // 否则用户在首页按一次返回（已武装）→ 进结果页 → 2 秒内再回首页，
    // 下一次按键会被当成第二次确认直接退出，等于把两次不相关的按键拼成一次确认。
    exitArmedAt.current = 0
  }, [pathname])

  useEffect(() => {
    if (!IS_MOBILE_SHELL) return

    let remove: (() => void) | undefined
    let disposed = false

    void (async () => {
      try {
        const { App } = await import('@capacitor/app')
        const handle = await App.addListener('backButton', ({ canGoBack }) => {
          const action = resolveBackAction(pathnameRef.current, canGoBack)

          if (action === 'back') {
            router.back()
            return
          }

          if (action === 'home') {
            // 冷启动直达 /result（例如从最近任务恢复）时历史里没有上一页，
            // back 会直接离开应用。直接回首页比退出更符合预期。
            router.replace('/')
            return
          }

          // 已在首页：Android 惯例是「再按一次退出」，避免误触直接关掉应用。
          // 只有监听器存在时 Capacitor 不会走默认的 goBack/退出，所以这里必须自己处理。
          const now = Date.now()
          if (now - exitArmedAt.current < EXIT_CONFIRM_MS) {
            void App.exitApp()
            return
          }
          exitArmedAt.current = now
          toast('再按一次返回键退出')
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
  }, [router])
}
