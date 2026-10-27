import { AlertTriangle, BookOpenText, KeyRound, ListChecks, Loader2, RefreshCw, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { summarizeUnconfiguredMessage } from '@/lib/capability-hint'
import { openApiSettings } from '@/hooks/use-api-settings'
import { useRateLimitCooldown } from '@/hooks/use-rate-limit-cooldown'
import { useHostname } from '@/hooks/use-hostname'
import { formatTimestamp } from '@/lib/format'
import {
  SUMMARY_DETAILS,
  SUMMARY_DETAIL_HINT,
  SUMMARY_DETAIL_LABEL,
  type Summary,
  type SummaryDetail,
} from '@/lib/types'
import { cn } from '@/lib/utils'
import type { UiError } from '@/stores/task-store'

/**
 * 总结详细度切换（P1-11）。
 *
 * 为什么是「切换即重新生成」而不是「本地裁剪」：精简档要的是更短的句子与更少的要点，
 * 只有重新问一次 LLM 才做得到；本地裁 keyPoints 只会得到「详细版被砍掉一半」，
 * 那不算精简。代价是一次额外调用，因此按档位分别缓存（见 task-store 的 summaryCacheKey）。
 *
 * 切换中的按钮禁用 + spinner，避免连点产生三次并发请求。
 */
export function SummaryDetailSwitch({
  value,
  onChange,
  busy = false,
  disabled = false,
}: {
  value: SummaryDetail
  onChange: (detail: SummaryDetail) => void
  busy?: boolean
  disabled?: boolean
}) {
  return (
    <div className="flex items-center gap-2" data-testid="summary-detail-switch">
      <span className="text-xs text-muted-foreground">详细度</span>
      <div role="group" aria-label="总结详细度" className="flex rounded-full border bg-muted/40 p-0.5">
        {SUMMARY_DETAILS.map((detail) => {
          const active = detail === value
          return (
            <button
              key={detail}
              type="button"
              data-testid={`summary-detail-${detail}`}
              aria-pressed={active}
              title={SUMMARY_DETAIL_HINT[detail]}
              disabled={disabled || busy}
              onClick={() => {
                if (!active) onChange(detail)
              }}
              className={cn(
                'rounded-full px-2.5 py-1 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60',
                active
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-accent hover:text-foreground',
              )}
            >
              {SUMMARY_DETAIL_LABEL[detail]}
            </button>
          )
        })}
      </div>
      {busy ? <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-hidden /> : null}
    </div>
  )
}

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
  const cooldown = useRateLimitCooldown(rateLimited, error.retryAfter)
  const retryBlocked = rateLimited && cooldown > 0
  // 这两种码的责任在用户自己的配置上：重试之前得先去改，光重试没用。
  // 与 retryable 不冲突——改完回来点重试是正常路径，所以两个按钮都留着。
  const userConfigFault = error.code === 'BYOK_KEY_INVALID' || error.code === 'BYOK_ENDPOINT_INVALID'

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
        <div className="flex flex-wrap items-center gap-2">
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
          {userConfigFault ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={openApiSettings}
              data-testid="summary-open-api-settings"
              className="gap-1.5"
            >
              <KeyRound className="size-3.5" aria-hidden />
              打开 API 设置
            </Button>
          ) : null}
        </div>
      ) : (
        <div className="flex flex-col items-start gap-2">
          {/* 未配置 Key 不是「等等就好」的错误，重试一万次也一样。
              给一条用户自己走得通的路：打开设置填自己的 Key。 */}
          <Button
            size="sm"
            variant="outline"
            onClick={openApiSettings}
            data-testid="summary-open-api-settings"
            className="gap-1.5"
          >
            <KeyRound className="size-3.5" aria-hidden />
            填写自己的 Key
          </Button>
          <p data-testid="summary-config-hint" className="text-xs text-muted-foreground">
            填完回到这里点「重试总结」即可，文字稿与导出不受影响。
          </p>
        </div>
      )}
    </div>
  )
}

/** 未总结引导态（关闭「解析后自动 AI 总结」后 / 没有可用 Key 时）：
 *  引导文案 + 档位选择 + 手动生成按钮。
 *
 *  disabled=true 表示「没有可用 Key」：此时按钮换成「填写自己的 Key」，
 *  因为重试永远不会成功，唯一出路是让用户去填一把自己的。 */
export function SummaryManualPrompt({
  onGenerate,
  disabled = false,
  detail,
  onDetailChange,
  busy = false,
  configIssue = null,
}: {
  onGenerate: () => void
  disabled?: boolean
  detail?: SummaryDetail
  onDetailChange?: (detail: SummaryDetail) => void
  busy?: boolean
  /**
   * 配置本身不自洽时的人话说明（如「填了地址却没填模型」）。
   *
   * 为什么单独传进来而不是这里自己读：只有结果页知道当前的门禁状态，
   * 由它统一取 `useByokStatus().configIssue` 传下来，避免同一个面板里
   * 出现两份可能不一致的判断。
   */
  configIssue?: string | null
}) {
  const hostname = useHostname()
  return (
    <div data-testid="summary-manual-prompt" className="flex flex-col items-start gap-3">
      <p className="text-sm leading-relaxed text-muted-foreground">
        {disabled
          ? '还没有可用的 AI 总结 Key，填一把自己的就能生成。'
          : '本次未生成 AI 总结。需要时可随时手动生成。'}
      </p>
      {configIssue ? (
        <p data-testid="summary-config-issue" className="text-xs leading-relaxed text-destructive">
          {configIssue}
        </p>
      ) : null}
      {detail && onDetailChange ? (
        <SummaryDetailSwitch value={detail} onChange={onDetailChange} busy={busy} disabled={disabled} />
      ) : null}
      {disabled ? (
        <Button
          data-testid="summary-open-api-settings"
          size="sm"
          onClick={openApiSettings}
          className="gap-1.5"
        >
          <KeyRound className="size-3.5" aria-hidden />
          填写自己的 Key
        </Button>
      ) : (
        <Button
          data-testid="manual-summarize-button"
          size="sm"
          onClick={onGenerate}
          disabled={busy}
          className="gap-1.5"
        >
          {busy ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
          ) : (
            <Sparkles className="size-3.5" aria-hidden />
          )}
          生成 AI 总结
        </Button>
      )}
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
