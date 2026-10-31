'use client'

import { useState } from 'react'
import { Download, RefreshCw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useMobileUpdate } from '@/hooks/use-mobile-update'
import { ANDROID_RELEASES_PAGE } from '@/lib/mobile-update'
import { openExternal } from '@/lib/external-link'
import { checkInstallPermission, requestInstallPermission, runInAppUpdate } from '@/lib/apk-installer'

/**
 * 设置面板里的「关于 / 检查更新」（仅 Android 壳）。
 *
 * ## 为什么放在设置里而不是顶栏
 *
 * 桌面端顶栏放得下一个「检查更新」图标，因为窗口宽、有鼠标。手机顶栏只有
 * 三四百像素，还要放主题切换、设置入口；再塞一个按钮就会挤成图标堆。
 * 而且手机上「更新 App」是个低频动作——打开设置才想起来，正合适。
 *
 * ## 为什么查到新版只给按钮不自动跳转
 *
 * 直接跳转或直接下载会打断用户当前正在做的事（可能正在等转写结果）。让他自己点。
 *
 * ## 为什么是「下载并安装」而不是「去浏览器下载」
 *
 * v0.1.7 之前这里调 `openExternal(apkUrl)`，用户点完跳出 App、落进浏览器，
 * 下载完还要自己找文件、点开、再回到 App。整个过程最需要用户耐心的地方
 * 恰恰是最容易被放弃的地方（浏览器里还要过一遍「是否允许下载」）。
 *
 * 现在下载走 `@capacitor/filesystem.downloadFile`（原生 HTTP，不受 Release
 * 资产 302 到 objects.githubusercontent.com 的 CORS 限制），下载完直接交给
 * 系统安装器。用户能少走「找文件」这一大步。
 *
 * **仍然做不到全自动**：Android 不允许应用静默安装自己，最后那次
 * 「是否安装」必须由系统弹窗问用户；首次还要去系统设置里开「安装未知应用」。
 * `android/app/src/main/java/com/videototext/app/ApkInstallerPlugin.java` 负责
 * 查授权、跳设置页、拉起安装器。
 *
 * ## 什么情况下退回浏览器
 *
 * 原生插件不可用（例如插件没打进包、或非 Android 环境）、下载失败、
 * 或者 Release 里根本没有 APK 资产时——都要有一条能走通的退路，
 * 否则用户就卡在一个死按钮上。
 */
export function MobileUpdateSection() {
  const update = useMobileUpdate()
  const [linkFailed, setLinkFailed] = useState(false)
  const [progress, setProgress] = useState<number | null>(null)
  const [hint, setHint] = useState<string | null>(null)
  const [installing, setInstalling] = useState(false)

  const apkUrl = update.release?.apkUrl ?? null
  const target = apkUrl ?? update.release?.pageUrl ?? ANDROID_RELEASES_PAGE

  /** 退路：交给系统浏览器/分享面板打开外部链接 */
  const fallbackToBrowser = async () => {
    setLinkFailed(false)
    const ok = await openExternal(target)
    if (!ok) setLinkFailed(true)
  }

  const handleDownload = async () => {
    setHint(null)
    setLinkFailed(false)

    // 没有 APK 直链时没什么可下载的，只能去 Release 页自己挑
    if (!apkUrl) {
      await fallbackToBrowser()
      return
    }

    const permission = await checkInstallPermission()
    if (!permission) {
      // 原生插件不可用（插件没打进包 / 非 Android）：保留浏览器路径
      await fallbackToBrowser()
      return
    }

    if (permission.needsPermission) {
      // 首次更新需要先去系统设置授权。跳过去，让用户回来再点。
      const opened = await requestInstallPermission()
      setHint(
        opened
          ? '需要在系统设置里允许「视频转文字」安装应用，开好之后回到这里再点一次「下载并安装」'
          : '打不开系统设置页，请手动在系统设置里允许本应用安装应用',
      )
      return
    }

    setInstalling(true)
    setProgress(0)
    const result = await runInAppUpdate(apkUrl, (percent) => setProgress(percent))
    setInstalling(false)
    setProgress(null)

    if (result.stage === 'failed') {
      // 下载或安装失败：给出原因，同时保留浏览器出口
      setHint(`${result.message ?? '下载失败'}。也可以去 Release 页手动下载。`)
      return
    }
    if (result.stage === 'need-permission') {
      setHint(result.message ?? '请先授权安装应用')
      return
    }
    setHint('安装包已交给系统，请在系统弹窗里点「安装」。')
  }

  return (
    <div className="space-y-2 rounded-lg border p-4" data-testid="mobile-update">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-0.5">
          <span className="text-sm font-medium">版本更新</span>
          <p className="text-xs text-muted-foreground" data-testid="mobile-current-version">
            当前版本 {update.currentVersion}
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant={update.status === 'available' ? 'default' : 'outline'}
          className="h-7 shrink-0 gap-1.5 px-2 text-xs"
          onClick={() => void update.check()}
          disabled={update.status === 'checking'}
          data-testid="mobile-check-update"
        >
          {update.status === 'available' ? (
            <Download className="size-3.5" aria-hidden />
          ) : (
            <RefreshCw className="size-3.5" aria-hidden />
          )}
          {update.status === 'checking' ? '检查中…' : '检查更新'}
        </Button>
      </div>

      {update.status === 'up-to-date' ? (
        <p className="text-xs text-emerald-600 dark:text-emerald-400" data-testid="mobile-update-status">
          已是最新版本。
        </p>
      ) : null}

      {update.status === 'available' ? (
        <div className="space-y-1.5" data-testid="mobile-update-status">
          <p className="text-xs">
            发现新版本 <span className="font-medium">{update.release?.version}</span>。
          </p>
          <p className="text-xs text-muted-foreground">
            可以直接在这里下载并安装，已保存的设置与最近转写记录都会保留。
          </p>
          <Button
            type="button"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => void handleDownload()}
            disabled={installing}
            data-testid="mobile-update-download"
          >
            <Download className="mr-1 size-3.5" aria-hidden />
            {installing ? (progress === null ? '准备中…' : `下载中 ${progress}%`) : '下载并安装'}
          </Button>
        </div>
      ) : null}

      {update.status === 'error' ? (
        <div className="space-y-1" data-testid="mobile-update-status">
          <p className="text-xs text-destructive">检查失败：{update.error}</p>
          <p className="text-xs text-muted-foreground">
            更新信息在 GitHub 上，国内网络可能连不上。可以去 Release 页手动下载。
          </p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 px-2 text-xs"
            onClick={() => void handleDownload()}
            data-testid="mobile-update-fallback"
          >
            {apkUrl ? '下载并安装' : '打开发布页'}
          </Button>
        </div>
      ) : null}

      {hint ? (
        <p className="text-xs text-muted-foreground" data-testid="mobile-update-hint">
          {hint}
        </p>
      ) : null}

      {linkFailed ? (
        <p className="text-xs text-destructive" data-testid="mobile-update-link-failed">
          没能打开浏览器。请手动访问 {ANDROID_RELEASES_PAGE}
        </p>
      ) : null}
    </div>
  )
}
