'use client'

import { useState } from 'react'
import { Download, RefreshCw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useMobileUpdate } from '@/hooks/use-mobile-update'
import { ANDROID_RELEASES_PAGE } from '@/lib/mobile-update'
import { openExternal } from '@/lib/external-link'

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
 * 直接跳浏览器会打断用户当前正在做的事（可能正在等转写结果）。让他自己点。
 */
export function MobileUpdateSection() {
  const update = useMobileUpdate()
  const [linkFailed, setLinkFailed] = useState(false)

  const target = update.release?.apkUrl ?? update.release?.pageUrl ?? ANDROID_RELEASES_PAGE

  const handleDownload = async () => {
    setLinkFailed(false)
    const ok = await openExternal(target)
    if (!ok) setLinkFailed(true)
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
            下载后用系统安装器覆盖安装即可，已保存的设置与最近转写记录都会保留。
          </p>
          <Button
            type="button"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => void handleDownload()}
            data-testid="mobile-update-download"
          >
            <Download className="mr-1 size-3.5" aria-hidden />
            下载新版本
          </Button>
        </div>
      ) : null}

      {update.status === 'error' ? (
        <div className="space-y-1" data-testid="mobile-update-status">
          <p className="text-xs text-destructive">检查失败：{update.error}</p>
          <p className="text-xs text-muted-foreground">
            更新信息在 GitHub 上，国内网络可能连不上。可以直接去 Release 页下载。
          </p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 px-2 text-xs"
            onClick={() => void handleDownload()}
            data-testid="mobile-update-fallback"
          >
            打开发布页
          </Button>
        </div>
      ) : null}

      {linkFailed ? (
        <p className="text-xs text-destructive" data-testid="mobile-update-link-failed">
          没能打开浏览器。请手动访问 {ANDROID_RELEASES_PAGE}
        </p>
      ) : null}
    </div>
  )
}
