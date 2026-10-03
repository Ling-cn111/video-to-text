'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { FileText, Home, ScrollText } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { TranscriptList } from '@/components/result/transcript-list'
import { SummaryCard } from '@/components/result/summary-card'
import { ErrorAlert } from '@/components/shared/error-alert'
import { PlatformBadge } from '@/components/shared/platform-badge'
import { formatDuration } from '@/lib/format'
import { useTaskStore } from '@/stores/task-store'

interface ResultViewProps {
  taskId: string
}

/** 结果页：左文字稿 / 右 AI 总结（<1024px 上下卡片流）；刷新后按 taskId 恢复 */
export function ResultView({ taskId }: ResultViewProps) {
  const router = useRouter()
  const phase = useTaskStore((state) => state.phase)
  const storeTaskId = useTaskStore((state) => state.taskId)
  const video = useTaskStore((state) => state.video)
  const transcript = useTaskStore((state) => state.transcript)
  const summary = useTaskStore((state) => state.summary)
  const error = useTaskStore((state) => state.error)
  const recover = useTaskStore((state) => state.recover)
  const retry = useTaskStore((state) => state.retry)
  const reset = useTaskStore((state) => state.reset)
  const [recovering, setRecovering] = useState(storeTaskId !== taskId)
  const [retrying, setRetrying] = useState(false)

  useEffect(() => {
    if (storeTaskId !== taskId) {
      setRecovering(true)
      void recover(taskId).finally(() => setRecovering(false))
    }
  }, [recover, storeTaskId, taskId])

  const handleRetry = async () => {
    setRetrying(true)
    const newTaskId = await retry()
    setRetrying(false)
    if (newTaskId) router.replace(`/processing/${newTaskId}`)
  }

  const handleBack = () => {
    reset()
    router.push('/')
  }

  if (recovering || phase === 'parsing' || phase === 'summarizing') {
    return <ResultSkeleton />
  }

  if (phase === 'error' && error) {
    return (
      <div className="mx-auto w-full max-w-2xl px-4 py-10">
        <ErrorAlert
          error={error}
          onBack={handleBack}
          onRetry={video ? handleRetry : undefined}
          retrying={retrying}
        />
      </div>
    )
  }

  if (phase !== 'done' || !transcript || !summary) {
    return <ResultSkeleton />
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-8 sm:py-10">
      {/* 头部：视频信息 + 返回 */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 flex-col gap-1.5">
          <h1 className="truncate text-lg font-semibold sm:text-xl">{video?.title ?? '转写结果'}</h1>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            {video ? <PlatformBadge platform={video.platform} /> : null}
            {video ? <span>{formatDuration(video.duration)}</span> : null}
            <span>共 {transcript.length} 段</span>
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={handleBack} className="shrink-0 gap-1.5 self-start sm:self-auto">
          <Home className="size-3.5" aria-hidden />
          返回首页
        </Button>
      </div>

      {/* 主体：桌面左右分栏，移动端上下卡片流 */}
      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[1.15fr_1fr]">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <ScrollText className="size-4 text-primary" aria-hidden />
              文字稿
              <Badge variant="secondary" className="ml-auto font-normal">
                点击时间可复制
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <TranscriptList items={transcript} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <FileText className="size-4 text-primary" aria-hidden />
              AI 总结
            </CardTitle>
          </CardHeader>
          <CardContent>
            <SummaryCard summary={summary} />
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function ResultSkeleton() {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-8 sm:py-10">
      <div className="flex flex-col gap-2">
        <Skeleton className="h-6 w-2/3" />
        <Skeleton className="h-4 w-40" />
      </div>
      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[1.15fr_1fr]">
        <Card>
          <CardContent className="flex flex-col gap-3 p-6">
            {[0, 1, 2, 3, 4, 5].map((index) => (
              <Skeleton key={index} className="h-4 w-full" style={{ width: `${92 - (index % 3) * 12}%` }} />
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex flex-col gap-3 p-6">
            <Skeleton className="h-20 w-full" />
            {[0, 1, 2].map((index) => (
              <Skeleton key={index} className="h-4 w-full" style={{ width: `${88 - index * 10}%` }} />
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
