'use client'

import { useCallback, useRef, useState } from 'react'

import {
  checkMobileUpdate,
  currentAppVersion,
  type LatestRelease,
} from '@/lib/mobile-update'

/**
 * 移动端「检查更新」的状态机。
 *
 * 比桌面端简单得多：没有下载、没有签名、没有重启，只有「查 → 有/没有/失败」。
 * 因为 Android 不允许静默安装，下载与安装必须交给系统浏览器 + 系统安装器。
 *
 * 刻意**不做**启动时自动检查：桌面端常驻后台，自动检查有意义；手机 App
 * 通常是打开用完就走，每次启动弹一个「有新版本」的对话框只会烦人。
 * 更新入口放在设置里，用户主动点才查。
 */
export type MobileUpdateStatus = 'idle' | 'checking' | 'up-to-date' | 'available' | 'error'

interface MobileUpdateState {
  status: MobileUpdateStatus
  release: LatestRelease | null
  error: string | null
}

const INITIAL: MobileUpdateState = { status: 'idle', release: null, error: null }

export function useMobileUpdate() {
  const [state, setState] = useState<MobileUpdateState>(INITIAL)
  const aliveRef = useRef(true)
  // 并发保护：连点「检查」不该发多个请求
  const busyRef = useRef(false)

  const check = useCallback(async (): Promise<void> => {
    if (busyRef.current) return
    busyRef.current = true
    aliveRef.current = true
    setState({ status: 'checking', release: null, error: null })

    const result = await checkMobileUpdate(currentAppVersion())
    if (!aliveRef.current) {
      busyRef.current = false
      return
    }

    if (result.kind === 'available') {
      setState({ status: 'available', release: result.release, error: null })
    } else if (result.kind === 'up-to-date') {
      setState({ status: 'up-to-date', release: null, error: null })
    } else {
      setState({ status: 'error', release: null, error: result.message })
    }
    busyRef.current = false
  }, [])

  const reset = useCallback(() => {
    setState(INITIAL)
    busyRef.current = false
  }, [])

  return { ...state, currentVersion: currentAppVersion(), check, reset }
}
