'use client'

import { useEffect, useState } from 'react'
import { Download, ExternalLink, RefreshCw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Progress } from '@/components/ui/progress'
import { useAppUpdate } from '@/hooks/use-app-update'
import { RELEASES_PAGE, truncateNotes } from '@/lib/updater'
import { IS_DESKTOP_SHELL } from '@/lib/runtime'
import { openExternal } from '@/lib/external-link'

/**
 * 顶栏的「检查更新」按钮。
 *
 * ## 为什么只在桌面壳渲染
 *
 * 网页端刷新就是最新版，Android 走 APK 覆盖安装，两者都没有「更新」这个概念。
 * 在这里放一个按钮会让人以为点一下就能升级网页。
 *
 * ## 为什么按钮不是一直可见
 *
 * 平时它是个不起眼的图标；只有真的查到新版本时才变成带文字的强调按钮。
 * 自动检查每 4 小时跑一次，绝大多数时候没有更新——常驻一个醒目按钮等于
 * 每次打开都在提醒用户「你可能有旧版本」。
 */
export function UpdateButton() {
  const [open, setOpen] = useState(false)
  const [manualError, setManualError] = useState<string | null>(null)
  // 外部链接打不开时兜底提示（桌面壳内 opener 插件缺失、浏览器拦截弹窗）
  const [linkFailed, setLinkFailed] = useState(false)
  const update = useAppUpdate()

  // 手动检查：把失败原因显示出来（自动检查是静默的）
  const handleManualCheck = async () => {
    setManualError(null)
    await update.check(false)
  }

  // 从 hook 的 error 同步到对话框。auto 检查失败时 status 会回到 idle，不会进这里
  useEffect(() => {
    if (update.status === 'error' && update.error) setManualError(update.error)
  }, [update.status, update.error])

  const handleOpenReleases = async () => {
    setLinkFailed(false)
    const ok = await openExternal(RELEASES_PAGE)
    if (!ok) setLinkFailed(true)
  }

  if (!IS_DESKTOP_SHELL) return null

  const hasUpdate = update.status === 'available' || update.status === 'downloading' || update.status === 'ready'

  const handleOpen = () => {
    setOpen(true)
    // 打开时若还没检查过，立刻查一次（不用等 8 秒的启动延迟）
    if (update.status === 'idle' || update.status === 'up-to-date' || update.status === 'error') {
      void update.check(false)
    }
  }

  const handleClose = (next: boolean) => {
    setOpen(next)
    if (!next) {
      setManualError(null)
      update.reset()
    }
  }

  return (
    <>
      <Button
        type="button"
        variant={hasUpdate ? 'default' : 'ghost'}
        size="sm"
        className="gap-1.5 px-2"
        onClick={handleOpen}
        data-testid="check-update"
        title={hasUpdate ? `有新版本 ${update.update?.version}` : '检查更新'}
      >
        {hasUpdate ? <Download className="size-4" aria-hidden /> : <RefreshCw className="size-4" aria-hidden />}
        <span className={hasUpdate ? 'inline' : 'hidden sm:inline'}>
          {hasUpdate ? `更新到 ${update.update?.version}` : '检查更新'}
        </span>
      </Button>
      <Dialog open={open} onOpenChange={handleClose}>
        <DialogContent className="sm:max-w-md" data-testid="update-dialog">
          <DialogHeader>
            <DialogTitle>软件更新</DialogTitle>
            <DialogDescription data-testid="update-description">
              {describe(update.status, update.update?.version)}
            </DialogDescription>
          </DialogHeader>

          {update.status === 'available' && update.update?.body ? (
            <pre
              className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded-md bg-muted p-3 text-xs leading-relaxed text-muted-foreground"
              data-testid="update-notes"
            >
              {truncateNotes(update.update.body)}
            </pre>
          ) : null}

          {update.status === 'downloading' ? (
            <div className="space-y-1.5" data-testid="update-progress">
              <Progress value={update.percent ?? undefined} className="h-2" />
              <p className="text-xs text-muted-foreground">
                {update.percent === null ? '正在下载…' : `已下载 ${update.percent}%`}
              </p>
            </div>
          ) : null}

          {manualError ? (
            <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive" data-testid="update-error">
              <p className="font-medium">检查失败</p>
              <p className="mt-1 break-all opacity-90">{manualError}</p>
              <p className="mt-2 opacity-90">
                更新服务器在 GitHub 上，国内网络可能连不上。可以手动去{' '}
                <button
                  type="button"
                  onClick={() => void handleOpenReleases()}
                  className="underline underline-offset-2"
                  data-testid="update-error-releases"
                >
                  Release 页
                </button>{' '}
                下载覆盖安装。
              </p>
            </div>
          ) : null}

          {linkFailed ? (
            <p className="text-xs text-destructive" data-testid="update-link-failed">
              没能打开浏览器。请手动访问 {RELEASES_PAGE}
            </p>
          ) : null}

          <DialogFooter className="gap-2">
            {update.status === 'available' ? (
              <Button type="button" onClick={() => void update.downloadAndInstall()} data-testid="update-download">
                下载并安装
              </Button>
            ) : null}
            {update.status === 'downloading' ? (
              <Button type="button" disabled>
                下载中…
              </Button>
            ) : null}
            {update.status === 'ready' ? (
              <Button type="button" onClick={() => void update.restart()} data-testid="update-restart">
                重启以完成更新
              </Button>
            ) : null}
            {update.status === 'checking' ? (
              <Button type="button" disabled>
                检查中…
              </Button>
            ) : null}
            {(update.status === 'idle' || update.status === 'up-to-date' || update.status === 'error') && !manualError ? (
              <Button type="button" onClick={() => void handleManualCheck()} data-testid="update-recheck">
                立即检查
              </Button>
            ) : null}
            <Button type="button" variant="outline" onClick={() => void handleOpenReleases()} data-testid="update-releases">
              <ExternalLink className="mr-1.5 size-3.5" aria-hidden />
              打开发布页
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

/** 按状态给出主文案。刻意不用「有可用更新」这类被动句，直接说结论 */
function describe(status: string, version?: string): string {
  switch (status) {
    case 'checking':
      return '正在检查是否有新版本…'
    case 'available':
      return `发现新版本 ${version ?? ''}，当前版本较旧。`
    case 'downloading':
      return `正在下载 ${version ?? ''}…`
    case 'ready':
      return '更新已下载完成，重启后生效。'
    case 'up-to-date':
      return '已是最新版本。'
    case 'unsupported':
      return '当前环境不支持自动更新，请手动下载。'
    default:
      return '点击「立即检查」查询是否有新版本。'
  }
}
