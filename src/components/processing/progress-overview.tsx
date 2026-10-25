'use client'

import { Progress } from '@/components/ui/progress'
import { formatRemaining, useRemainingSeconds } from '@/hooks/use-remaining-time'
import { TASK_STAGES, type TaskStage } from '@/lib/types'

interface ProgressOverviewProps {
  progress: number
  stage: TaskStage | null
}

/** 整体进度：百分比 + 进度条 + 当前阶段说明 + 本阶段剩余时间预估（P1-8） */
export function ProgressOverview({ progress, stage }: ProgressOverviewProps) {
  const stageMeta = TASK_STAGES.find((item) => item.id === stage)
  const remaining = useRemainingSeconds(stage, progress, progress < 100)
  const remainingText = formatRemaining(remaining)

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-medium">
          {stageMeta ? stageMeta.label : '准备中'}…
        </span>
        <span className="text-2xl font-bold tabular-nums text-primary">{progress}%</span>
      </div>
      <Progress value={progress} className="h-2" aria-label={`转写进度 ${progress}%`} />
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="text-xs text-muted-foreground">
          {stageMeta ? stageMeta.description : '正在准备任务'}
        </p>
        {remainingText ? (
          <p data-testid="remaining-time" className="text-xs tabular-nums text-muted-foreground">
            {remainingText}
          </p>
        ) : null}
      </div>
    </div>
  )
}
