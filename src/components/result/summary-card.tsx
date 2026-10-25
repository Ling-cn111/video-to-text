import { AlertTriangle, BookOpenText, ListChecks, Loader2, RefreshCw, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { summarizeUnconfiguredMessage } from '@/lib/capability-hint'
import { useRateLimitCooldown } from '@/hooks/use-rate-limit-cooldown'
import { useHostname } from '@/hooks/use-hostname'
import { formatTimestamp } from '@/lib/format'
import type { Summary } from '@/lib/types'
import type { UiError } from '@/stores/task-store'

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

/** 总结生成失败（P0 降级态）：错误卡 + 重试按钮；文字稿区域不受影响。
 *  retryable=false（如 LLM Key 未配置，M2-P2-A）时隐藏重试按钮，改为配置引导。
 *
 *  为什么收整个 UiError 而不是 message 字符串：429 限流和普通服务异常的处理方式完全不同
 *  ——限流要给冷却倒计时并临时禁用重试（重试本身也是 POST，冷却期内点必然再次 429），
 *  而这只在拿到错误码时才做得到。 */
export function SummaryErrorState({
  error,
  onRetry,
  retrying,
  retryable = true,
}: {
  error: UiError
  onRetry: () => void
  retrying: boolean
  retryable?: boolean
}) {
  const rateLimited = error.code === 'RATE_LIMITED'
  const cooldown = useRateLimitCooldown(rateLimited)
  const retryBlocked = rateLimited && cooldown > 0

  return (
    <div
      data-testid="summary-error"
      className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4"
    >
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
        <div className="flex flex-col gap-0.5">
          <p className="text-sm font-medium">{rateLimited ? '请求太频繁' : '总结生成失败'}</p>
          <p className="text-xs leading-relaxed text-muted-foreground">{error.message}</p>
          {rateLimited ? (
            <p data-testid="rate-limit-hint" className="text-xs leading-relaxed text-muted-foreground">
              {retryBlocked
                ? `为避免继续触发限制，约 ${cooldown} 秒后可重试`
                : '冷却结束，现在可以重试了'}
            </p>
          ) : null}
        </div>
      </div>
      {retryable ? (
        <Button
          size="sm"
          variant="outline"
          onClick={onRetry}
          disabled={retrying || retryBlocked}
          className="gap-1.5"
        >
          {retrying ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="size-3.5" aria-hidden />
          )}
          重试总结
        </Button>
      ) : (
        <p data-testid="summary-config-hint" className="text-xs text-muted-foreground">
          配置完成后刷新页面即可生成总结。
        </p>
      )}
    </div>
  )
}

/** 未总结引导态（关闭「解析后自动 AI 总结」后）：引导文案 + 手动生成按钮。
 *  summarizeConfigured=false（M2-P2-A）时按钮禁用并给出配置引导。 */
export function SummaryManualPrompt({
  onGenerate,
  disabled = false,
}: {
  onGenerate: () => void
  disabled?: boolean
}) {
  const hostname = useHostname()
  return (
    <div data-testid="summary-manual-prompt" className="flex flex-col items-start gap-3">
      <p className="text-sm leading-relaxed text-muted-foreground">
        本次未生成 AI 总结。需要时可随时手动生成。
      </p>
      <Button
        data-testid="manual-summarize-button"
        size="sm"
        onClick={onGenerate}
        disabled={disabled}
        className="gap-1.5"
      >
        <Sparkles className="size-3.5" aria-hidden />
        生成 AI 总结
      </Button>
      {disabled ? (
        <p data-testid="manual-summarize-config-hint" className="text-xs leading-relaxed text-muted-foreground">
          {/* 文案分环境：本地给配置指引，公网只说「暂不可用 + 文字稿不受影响」 */}
          {hostname === null ? 'AI 总结暂不可用，文字稿与导出不受影响' : summarizeUnconfiguredMessage(hostname)}
        </p>
      ) : null}
    </div>
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
