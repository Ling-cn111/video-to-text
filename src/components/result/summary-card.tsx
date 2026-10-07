import { AlertTriangle, BookOpenText, ListChecks, Loader2, RefreshCw, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { formatTimestamp } from '@/lib/format'
import type { Summary } from '@/lib/types'

/** AI 总结卡：一句话概要 + 要点 + 章节笔记 */
export function SummaryCard({ summary }: { summary: Summary }) {
  return (
    <div className="flex flex-col gap-5">
      <section className="flex flex-col gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
          <Sparkles className="size-4 text-primary" aria-hidden />
          一句话概要
        </h3>
        <p className="rounded-xl border border-primary/20 bg-primary/5 p-4 text-sm leading-relaxed">
          {summary.summary}
        </p>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
          <ListChecks className="size-4 text-primary" aria-hidden />
          核心要点
        </h3>
        <ol className="flex flex-col gap-2.5" data-testid="key-points">
          {summary.keyPoints.map((point, index) => (
            <li key={index} className="flex items-start gap-2.5">
              <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">
                {index + 1}
              </span>
              <p className="text-sm leading-relaxed">
                <span className="mr-1.5 rounded-md border bg-muted/60 px-1.5 py-0.5 font-mono text-xs tabular-nums text-muted-foreground">
                  {formatTimestamp(point.time)}
                </span>
                {point.text}
              </p>
            </li>
          ))}
        </ol>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
          <BookOpenText className="size-4 text-primary" aria-hidden />
          章节笔记
        </h3>
        <ol className="flex flex-col" data-testid="chapter-timeline">
          {summary.chapters.map((chapter, index) => (
            <li key={index} className="relative flex gap-3 pb-4 last:pb-0">
              {index < summary.chapters.length - 1 ? (
                <span aria-hidden className="absolute left-[27px] top-6 h-[calc(100%-24px)] w-0.5 rounded bg-border" />
              ) : null}
              <span className="shrink-0 rounded-md border bg-muted/60 px-1.5 py-0.5 font-mono text-xs tabular-nums text-muted-foreground">
                {formatTimestamp(chapter.timeStart)}–{formatTimestamp(chapter.timeEnd)}
              </span>
              <div className="flex flex-col gap-0.5">
                <span className="text-sm font-medium">{chapter.title}</span>
                <p className="text-xs leading-relaxed text-muted-foreground">{chapter.note}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}

/** 总结生成失败（P0 降级态）：错误卡 + 重试按钮；文字稿区域不受影响 */
export function SummaryErrorState({
  message,
  onRetry,
  retrying,
}: {
  message: string
  onRetry: () => void
  retrying: boolean
}) {
  return (
    <div
      data-testid="summary-error"
      className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4"
    >
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
        <div className="flex flex-col gap-0.5">
          <p className="text-sm font-medium">总结生成失败</p>
          <p className="text-xs leading-relaxed text-muted-foreground">{message}</p>
        </div>
      </div>
      <Button size="sm" variant="outline" onClick={onRetry} disabled={retrying} className="gap-1.5">
        {retrying ? (
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
        ) : (
          <RefreshCw className="size-3.5" aria-hidden />
        )}
        重试总结
      </Button>
    </div>
  )
}

/** 总结空态：无总结且非加载中（正常流程不应出现，兜底防回归） */
export function SummaryEmptyState() {
  return (
    <p data-testid="summary-empty" className="text-sm text-muted-foreground">
      暂无总结内容。
    </p>
  )
}

/** 总结加载中骨架（文字稿已可读时，右卡占位） */
export function SummarySkeleton() {
  return (
    <div data-testid="summary-skeleton" className="flex flex-col gap-4">
      <Skeleton className="h-16 w-full rounded-xl" />
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-2/3" />
      <Skeleton className="h-4 w-5/6" />
    </div>
  )
}
