import Link from 'next/link'
import { FileQuestion } from 'lucide-react'
import { Button } from '@/components/ui/button'

/** 404 页：此前落到 Next 默认的英文 "This page could not be found"。 */
export default function NotFound() {
  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-16" data-testid="not-found">
      <div className="rounded-xl border bg-card p-8 text-center shadow-sm">
        <div className="mx-auto flex size-12 items-center justify-center rounded-full bg-muted">
          <FileQuestion className="size-6 text-muted-foreground" aria-hidden />
        </div>
        <h1 className="mt-4 text-lg font-semibold">页面不存在</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          链接可能过期、被改动过，或者这个页面本来就不存在。
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          转写任务的链接形如 <code className="rounded bg-muted px-1 py-0.5 text-xs">/result?id=…</code>，
          缺了 <code className="rounded bg-muted px-1 py-0.5 text-xs">id</code> 就找不到结果。
        </p>
        <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
          <Button asChild>
            <Link href="/">返回首页</Link>
          </Button>
        </div>
      </div>
    </div>
  )
}
