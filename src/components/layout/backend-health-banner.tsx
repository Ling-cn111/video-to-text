'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, X } from 'lucide-react'
import { api } from '@/lib/api'

/** 健康检查节奏：挂载即探测一次，之后每 15 秒一次；单次 3 秒超时（后端未启动时快速给出结论） */
const CHECK_INTERVAL_MS = 15_000
const CHECK_TIMEOUT_MS = 3_000
/** 横幅实测高度：占位块与顶栏 sticky 偏移共用（隐藏时移除，回落到 0px） */
const OFFSET_VAR = '--backend-banner-offset'

/**
 * 全局「后端不可达」横幅（任务 M2-P3）。
 *
 * 真实模式下后端未启动、或 PWA 离线外壳打开页面时，界面照常渲染但任何操作都会失败——
 * 本横幅给出明确指引（启动本地服务 / 查看 run.bat）；后端恢复后自动隐藏，也可手动关闭。
 * 端点按模式分流：真实模式 /backend-api/health（后端 GET /api/health），Mock 模式 /api/health。
 */
export function BackendHealthBanner() {
  const bannerRef = useRef<HTMLDivElement>(null)
  const [unreachable, setUnreachable] = useState(false)
  const [dismissed, setDismissed] = useState(false)

  const check = useCallback(async () => {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), CHECK_TIMEOUT_MS)
    try {
      const health = await api.healthCheck(controller.signal)
      setUnreachable(health.status !== 'ok')
    } catch {
      // 网络错误 / 超时 / 非 2xx 一律视为不可达
      setUnreachable(true)
    } finally {
      clearTimeout(timeout)
    }
  }, [])

  useEffect(() => {
    void check()
    const timer = setInterval(() => void check(), CHECK_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [check])

  // 恢复可达后清除「手动关闭」状态：下次故障重新提示
  useEffect(() => {
    if (!unreachable) setDismissed(false)
  }, [unreachable])

  const visible = unreachable && !dismissed

  // 横幅为 fixed（不占文档流）：把实测高度写入 CSS 变量，占位块与顶栏 sticky 偏移据此对齐
  useEffect(() => {
    const root = document.documentElement
    const element = bannerRef.current
    if (!visible || !element) {
      root.style.removeProperty(OFFSET_VAR)
      return
    }
    const apply = () => root.style.setProperty(OFFSET_VAR, `${element.offsetHeight}px`)
    apply()
    const observer = new ResizeObserver(apply)
    observer.observe(element)
    return () => {
      observer.disconnect()
      root.style.removeProperty(OFFSET_VAR)
    }
  }, [visible])

  if (!visible) return null

  return (
    <>
      <div
        ref={bannerRef}
        role="alert"
        data-testid="backend-unreachable-banner"
        className="fixed inset-x-0 top-0 z-50 flex items-center gap-2 bg-destructive px-3 py-2 text-destructive-foreground shadow-md sm:px-4"
      >
        <AlertTriangle className="size-4 shrink-0" aria-hidden />
        <p className="flex-1 text-xs leading-snug sm:text-sm">
          后端服务不可达，请确认已启动本地服务（运行 run.bat start）
        </p>
        <button
          type="button"
          aria-label="关闭提示"
          onClick={() => setDismissed(true)}
          className="shrink-0 rounded-md p-1 transition-colors hover:bg-destructive-foreground/20"
        >
          <X className="size-4" aria-hidden />
        </button>
      </div>
      {/* 占位：横幅 fixed 不占文档流，这里撑出等高空间，保证页面内容与顶栏不被遮挡 */}
      <div aria-hidden className="shrink-0" style={{ height: `var(${OFFSET_VAR}, 0px)` }} />
    </>
  )
}
