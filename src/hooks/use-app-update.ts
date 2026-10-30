'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

import {
  CHECK_TIMEOUT_MS,
  FIRST_CHECK_DELAY_MS,
  RECHECK_INTERVAL_MS,
  isNewerVersion,
  shouldEnableUpdater,
  type UpdateInfo,
} from '@/lib/updater'
import { IS_DESKTOP_SHELL } from '@/lib/runtime'

/**
 * 自动更新的状态机。
 *
 * `idle` → `checking` → （`available` | `up-to-date` | `error`）
 * `available` → `downloading` → （`ready` | `error`）
 *
 * 为什么状态放这里而不是 store：它只被顶栏的更新按钮与更新对话框用，
 * 页面间不共享，放 Zustand 只会让 store 更臃肿。
 */
export type UpdateStatus =
  | 'idle'
  | 'checking'
  | 'available'
  | 'up-to-date'
  | 'downloading'
  | 'ready'
  | 'error'
  /** 当前环境不支持（网页端 / Android 壳 / Tauri 桥不存在） */
  | 'unsupported'

interface UpdateState {
  status: UpdateStatus
  update: UpdateInfo | null
  /** 下载进度百分比；`null` 表示总量未知 */
  percent: number | null
  /** 错误信息（仅用户主动检查时展示） */
  error: string | null
}

const INITIAL: UpdateState = { status: 'idle', update: null, percent: null, error: null }

/** Tauri updater 的 Update 实例，只在桌面壳内存在 */
interface TauriUpdate {
  version: string
  currentVersion: string
  date?: string
  body?: string
  downloadAndInstall: (
    onEvent?: (event: { event: string; data?: { chunkLength?: number; contentLength?: number } }) => void,
    options?: { timeout?: number },
  ) => Promise<void>
  close: () => Promise<void>
}

/**
 * 是否在 Tauri 运行时里。
 *
 * `IS_DESKTOP_SHELL` 是构建期常量，但它只说明「这份产物是给桌面端的」——
 * Playwright 把同一个 `out/` 起成静态站点跑测试时，常量是真的、Tauri 桥是假的。
 * 所以真正调用前必须再查一次运行时。
 */
function hasTauriBridge(): boolean {
  if (typeof window === 'undefined') return false
  return '__TAURI_INTERNALS__' in window
}

/**
 * 自动更新。
 *
 * @param options.silent 自动检查为 true（失败不弹提示），手动检查为 false
 */
export function useAppUpdate() {
  const [state, setState] = useState<UpdateState>(INITIAL)
  // Update 实例必须留在 ref 里：它持有 Rust 侧的资源句柄，重新渲染不能丢
  const updateRef = useRef<TauriUpdate | null>(null)
  // 组件卸载后不再 setState，避免开发期的 "setState on unmounted" 噪音
  const aliveRef = useRef(true)

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
      // 释放 Rust 侧资源；失败无所谓，进程退出时也会回收
      void updateRef.current?.close().catch(() => {})
      updateRef.current = null
    }
  }, [])

  const safeSet = useCallback((next: UpdateState | ((prev: UpdateState) => UpdateState)) => {
    if (!aliveRef.current) return
    setState(next)
  }, [])

  /**
   * 检查更新。
   *
   * `silent` 决定失败时是否写 `error`：自动检查（启动 8 秒后）静默失败，
   * 手动检查（点按钮）把原因显示出来。这是整个模块最重要的取舍——见
   * `src/lib/updater.ts` 顶部注释。
   */
  const check = useCallback(
    async (silent = true): Promise<void> => {
      if (!shouldEnableUpdater({ isDesktopShell: IS_DESKTOP_SHELL, isProduction: process.env.NODE_ENV === 'production' })) {
        safeSet({ ...INITIAL, status: 'unsupported' })
        return
      }
      if (!hasTauriBridge()) {
        safeSet({ ...INITIAL, status: 'unsupported' })
        return
      }

      safeSet((prev) => ({ ...prev, status: 'checking', error: null }))

      try {
        // 动态 import：Node 环境（E2E）下这个模块不会被求值
        const { check: checkUpdate } = await import('@tauri-apps/plugin-updater')
        const update = (await checkUpdate({ timeout: CHECK_TIMEOUT_MS })) as TauriUpdate | null

        if (!aliveRef.current) return

        if (!update) {
          safeSet({ status: 'up-to-date', update: null, percent: null, error: null })
          return
        }

        // 双保险：插件本身已按版本比较，这里再比一次，防止端点返回降级版本
        // 时被当成「有更新」（Tauri 默认允许同版本，但降级要显式开 allow-downgrades）
        const { getVersion } = await import('@tauri-apps/api/app')
        const current = await getVersion()
        if (!isNewerVersion(update.version, current)) {
          await update.close().catch(() => {})
          safeSet({ status: 'up-to-date', update: null, percent: null, error: null })
          return
        }

        updateRef.current = update
        safeSet({
          status: 'available',
          update: {
            version: update.version,
            currentVersion: current,
            date: update.date,
            body: update.body,
          },
          percent: null,
          error: null,
        })
      } catch (error) {
        if (!aliveRef.current) return
        const message = error instanceof Error ? error.message : String(error)
        // 静默失败：状态回到 idle，界面不显示任何东西
        safeSet(silent ? INITIAL : { ...INITIAL, status: 'error', error: message })
      }
    },
    [safeSet],
  )

  /** 下载并安装。成功后 Windows 会由安装器接管并重启应用 */
  const downloadAndInstall = useCallback(async (): Promise<void> => {
    const update = updateRef.current
    if (!update) return

    safeSet((prev) => ({ ...prev, status: 'downloading', percent: null, error: null }))

    let downloaded = 0
    let total: number | undefined
    try {
      await update.downloadAndInstall((event) => {
        if (event.event === 'Started') {
          total = event.data?.contentLength
          safeSet((prev) => ({ ...prev, percent: total ? 0 : null }))
        } else if (event.event === 'Progress') {
          downloaded += event.data?.chunkLength ?? 0
          const percent = total ? Math.min(100, Math.round((downloaded / total) * 100)) : null
          safeSet((prev) => ({ ...prev, percent }))
        }
      })
      safeSet((prev) => ({ ...prev, status: 'ready', percent: 100 }))
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      safeSet({ ...INITIAL, status: 'error', error: message })
    }
  }, [safeSet])

  /**
   * 安装完成后重启。
   *
   * Windows 上 `downloadAndInstall` 会自己拉起安装器并退出进程，通常走不到这里；
   * 其余平台（以及 `restartAfterInstall: false` 的情况）需要主动 relaunch。
   */
  const restart = useCallback(async (): Promise<void> => {
    try {
      const { relaunch } = await import('@tauri-apps/plugin-process')
      await relaunch()
    } catch {
      // 重启失败时提示用户手动重开，不弹错误对话框（此时更新其实已装好）
    }
  }, [])

  /** 关闭对话框时清掉「已是最新 / 出错」的临时状态，避免下次打开还显示上次结果 */
  const reset = useCallback(() => {
    safeSet((prev) => (prev.status === 'available' || prev.status === 'downloading' ? prev : INITIAL))
  }, [safeSet])

  // 启动后延迟检查一次，之后定时复查。只在桌面壳且生产构建下挂载定时器。
  useEffect(() => {
    if (!shouldEnableUpdater({ isDesktopShell: IS_DESKTOP_SHELL, isProduction: process.env.NODE_ENV === 'production' })) {
      return
    }
    if (!hasTauriBridge()) return

    const first = setTimeout(() => {
      void check(true)
    }, FIRST_CHECK_DELAY_MS)
    const interval = setInterval(() => {
      // 已经发现更新 / 正在下载时不要重复检查，否则会把 ref 里的实例换掉
      if (updateRef.current) return
      void check(true)
    }, RECHECK_INTERVAL_MS)

    return () => {
      clearTimeout(first)
      clearInterval(interval)
    }
  }, [check])

  return {
    ...state,
    check,
    downloadAndInstall,
    restart,
    reset,
  }
}
