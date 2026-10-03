import { BookOpenText, ListChecks, Sparkles } from 'lucide-react'
import { Separator } from '@/components/ui/separator'
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
        <ol className="flex flex-col gap-2.5">
          {summary.keyPoints.map((point, index) => (
            <li key={index} className="flex items-start gap-2.5">
              <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">
                {index + 1}
              </span>
              <p className="text-sm leading-relaxed">{point}</p>
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
        <ol className="flex flex-col">
          {summary.chapters.map((chapter, index) => (
            <li key={index} className="relative flex gap-3 pb-4 last:pb-0">
              {index < summary.chapters.length - 1 ? (
                <span aria-hidden className="absolute left-[27px] top-6 h-[calc(100%-24px)] w-0.5 rounded bg-border" />
              ) : null}
              <span className="shrink-0 rounded-md border bg-muted/60 px-1.5 py-0.5 font-mono text-xs tabular-nums text-muted-foreground">
                {formatTimestamp(chapter.time)}
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
