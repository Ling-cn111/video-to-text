import { AudioLines } from 'lucide-react'

/** 全局顶栏：品牌 +（PWA 安装按钮在 feat/pwa 中加入） */
export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b bg-background/80 backdrop-blur">
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between px-4">
        <div className="flex items-center gap-2">
          <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <AudioLines className="size-4" aria-hidden />
          </div>
          <span className="text-base font-semibold tracking-tight">视频转文字</span>
        </div>
      </div>
    </header>
  )
}
