'use client'

import { AudioLines, Check, Link2, Mic, Sparkles } from 'lucide-react'
import { TASK_STAGES, type TaskStage } from '@/lib/types'
import { cn } from '@/lib/utils'

const STAGE_ICONS = {
  parse_link: Link2,
  extract_audio: AudioLines,
  asr: Mic,
  summarize: Sparkles,
} as const

interface StageStepsProps {
  /** 当前阶段；完成后为 summarize 且整体进入 done */
  current: TaskStage | null
  /** 整体是否已完成（所有阶段打勾） */
  completed?: boolean
}

/** 四阶段步进条：解析链接 → 提取音频 → 语音识别 → 生成总结 */
export function StageSteps({ current, completed = false }: StageStepsProps) {
  const currentIndex = TASK_STAGES.findIndex((stage) => stage.id === current)

  return (
    <ol className="flex flex-col gap-0" aria-label="转写阶段">
      {TASK_STAGES.map((stage, index) => {
        const isDone = completed || (currentIndex > -1 && index < currentIndex)
        const isCurrent = !completed && stage.id === current
        const Icon = STAGE_ICONS[stage.id]

        return (
          <li key={stage.id} className="relative flex gap-3 pb-5 last:pb-0">
            {index < TASK_STAGES.length - 1 ? (
              <span
                aria-hidden
                className={cn(
                  'absolute left-[15px] top-8 h-[calc(100%-32px)] w-0.5 rounded',
                  isDone ? 'bg-primary' : 'bg-border',
                )}
              />
            ) : null}
            <span
              className={cn(
                'flex size-8 shrink-0 items-center justify-center rounded-full border-2 transition-colors',
                isDone && 'border-primary bg-primary text-primary-foreground',
                isCurrent && 'border-primary bg-primary/10 text-primary',
                !isDone && !isCurrent && 'border-border bg-muted text-muted-foreground',
              )}
            >
              {isDone ? (
                <Check className="size-4" aria-hidden />
              ) : isCurrent ? (
                <span className="size-2.5 animate-pulse rounded-full bg-primary" aria-hidden />
              ) : (
                <Icon className="size-4" aria-hidden />
              )}
            </span>
            <div className="flex flex-col gap-0.5 pt-0.5">
              <span
                className={cn(
                  'text-sm font-medium leading-6',
                  isCurrent ? 'text-primary' : isDone ? 'text-foreground' : 'text-muted-foreground',
                )}
              >
                {stage.label}
                {isCurrent ? <span className="ml-2 text-xs text-muted-foreground">进行中…</span> : null}
              </span>
              <span className="text-xs leading-relaxed text-muted-foreground">{stage.description}</span>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
