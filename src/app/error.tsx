'use client'

import Link from 'next/link'
import { AlertTriangle, RotateCw } from 'lucide-react'
import { Button } from '@/components/ui/button'

/**
 * 路由级错误边界（Next App Router 的 error.tsx）。
 *
 * 此前任何渲染异常都会整页白屏，用户看不到任何信息也不知道能做什么。
 * 这里给出「哪一步出错 + 重试 + 回首页」三条出路，并把 digest 显示出来
 * （生产环境 Next 会把真实错误隐藏，只留 digest，方便用户截图报障）。
 */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-16" data-testid="route-error">
      <div className="rounded-xl border bg-card p-8 text-center shadow-sm">
        <div className="mx-auto flex size-12 items-center justify-center rounded-full bg-destructive/10">
          <AlertTriangle className="size-6 text-destructive" aria-hidden />
        </div>
        <h1 className="mt-4 text-lg font-semibold">页面出错了</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          这不是你的操作问题。可以先重试一次；如果反复出现，请把下面的编号一起反馈。
        </p>
        {error.digest ? (
          <p className="mt-3 font-mono text-xs text-muted-foreground">
            错误编号：<span data-testid="route-error-digest">{error.digest}</span>
          </p>
        ) : null}
        <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
          <Button onClick={reset} className="gap-1.5">
            <RotateCw className="size-4" aria-hidden />
            重试
          </Button>
          <Button variant="outline" asChild>
            <Link href="/">返回首页</Link>
          </Button>
        </div>
      </div>
    </div>
  )
}
