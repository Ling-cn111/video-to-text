import Link from 'next/link'
import { AudioLines } from 'lucide-react'
import { InstallAppButton } from '@/components/pwa/install-app-button'
import { ThemeToggle } from '@/components/layout/theme-toggle'
import { ApiSettingsButton } from '@/components/layout/api-settings-button'

/** 全局顶栏：品牌 + API 设置 + 主题切换 + PWA 安装入口。
 *  sticky 偏移跟随「后端不可达」横幅高度（--backend-banner-offset，未设置时回落 0px），
 *  横幅出现时顶栏整体下移，滚动中不会被横幅盖住。 */
export function SiteHeader() {
  return (
    <header
      className="sticky z-40 border-b bg-background/80 backdrop-blur"
      style={{ top: "var(--backend-banner-offset, 0px)" }}
    >
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between px-4">
        <Link
          href="/"
          className="flex items-center gap-2 rounded-md focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <AudioLines className="size-4" aria-hidden />
          </div>
          <span className="text-base font-semibold tracking-tight">视频转文字</span>
        </Link>
        <div className="flex items-center gap-1">
          <ApiSettingsButton />
          <ThemeToggle />
          <InstallAppButton />
        </div>
      </div>
    </header>
  )
}
