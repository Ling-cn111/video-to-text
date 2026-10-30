import Link from 'next/link'
import { AudioLines } from 'lucide-react'
import { InstallAppButton } from '@/components/pwa/install-app-button'
import { ThemeToggle } from '@/components/layout/theme-toggle'
import { ApiSettingsButton } from '@/components/layout/api-settings-button'

/** 全局顶栏：品牌 + API 设置 + 主题切换 + PWA 安装入口。
 *  sticky 偏移 = 安全区顶部（状态栏）+「后端不可达」横幅高度。
 *
 *  为什么必须加 `--safe-top`：sticky 是相对滚动视口定位的，不会跟着 body 的
 *  padding 走。只给 body 加 padding-top 的话，首屏位置对了，但一滚动顶栏就会
 *  滑到状态栏底下 —— 真机反馈的「顶部与手机的 UI 重叠」正是这个。
 *  `--backend-banner-offset` 未设置时回落 0px（横幅隐藏时移除该变量）。 */
export function SiteHeader() {
  return (
    <header
      className="sticky z-40 border-b bg-background/80 backdrop-blur"
      style={{ top: "calc(var(--safe-top, 0px) + var(--backend-banner-offset, 0px))" }}
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
