'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Home } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { VideoCard } from '@/components/processing/video-card'
import { StageSteps } from '@/components/processing/stage-steps'
import { ProgressOverview } from '@/components/processing/progress-overview'
import { ErrorAlert } from '@/components/shared/error-alert'
import { useTaskPolling } from '@/hooks/use-task-polling'
import { useLeaveGuard } from '@/hooks/use-leave-guard'
import { processingHref } from '@/lib/routes'
import { useTaskStore } from '@/stores/task-store'

interface ProcessingViewProps {
  taskId: string
}

/** 转写进度页：视频信息卡 + 四阶段步进 + 整体进度；支持直链恢复与失败重试 */
export function ProcessingView({ taskId }: ProcessingViewProps) {
  const router = useRouter()
  const phase = useTaskStore((state) => state.phase)
  const storeTaskId = useTaskStore((state) => state.taskId)
  const video = useTaskStore((state) => state.video)
  const stage = useTaskStore((state) => state.stage)
  const progress = useTaskStore((state) => state.progress)
  const error = useTaskStore((state) => state.error)
  const recover = useTaskStore((state) => state.recover)
  const retry = useTaskStore((state) => state.retry)
  const reset = useTaskStore((state) => state.reset)
  const [retrying, setRetrying] = useState(false)

  useTaskPolling(taskId)
  // P1-8：转写进行中时，浏览器级离开（关标签 / 刷新）先确认一次
  useLeaveGuard(phase === 'transcribing' || phase === 'summarizing')

  const needsRecover = storeTaskId !== taskId
  useEffect(() => {
    if (needsRecover) void recover(taskId)
  }, [needsRecover, recover, taskId])

  const handleRetry = async () => {
    setRetrying(true)
    const newTaskId = await retry()
    setRetrying(false)
    if (newTaskId) router.replace(processingHref(newTaskId))
  }

  const handleBack = () => {
    reset()
    router.push('/')
  }

  /**
   * 停止等待（P1-8）。
   *
   * 为什么是「停止等待」而不是「取消任务」：真正的取消需要后端在执行中途
   * 协作退出（下载 / FFmpeg / ASR 三段都是长阻塞调用，没有任何取消检查点），
   * 只把任务标成 failed 并不会让正在跑的转写停下来 —— 那只是骗自己。
   * 这里做的是用户真正想要的那件事：不再守着这个页面，但任务照常跑完，
   * 回首页「最近转写」还能找回来（P1-7）。
   */
  const handleStopWaiting = () => {
    reset()
    router.push('/')
  }

  if (needsRecover && (phase === 'idle' || phase === 'parsing')) {
    return <ProcessingSkeleton />
  }

  if (phase === 'error' && error) {
    return (
      <div className="mx-auto w-full max-w-2xl px-4 py-10">
        <ErrorAlert error={error} onRetry={handleRetry} retrying={retrying} onBack={handleBack} />
        {video ? (
          <Card className="mt-6">
            <CardContent className="p-5 opacity-60">
              <VideoCard video={video} />
            </CardContent>
          </Card>
        ) : null}
      </div>
    )
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-4 py-8 sm:py-12">
      {video ? (
        <Card>
          <CardContent className="p-5">
            <VideoCard video={video} />
          </CardContent>
        </Card>
      ) : null}
      <Card>
        <CardContent className="flex flex-col gap-6 p-6">
          <ProgressOverview progress={progress} stage={stage} />
          <div className="h-px bg-border" aria-hidden />
          <StageSteps current={stage} completed={phase === 'summarizing'} />
        </CardContent>
      </Card>

      {/* 离开出口（P1-8）：长任务不该把用户钉在页面上。
          文案说清「任务不会停」，否则用户会以为走了就白等了。 */}
      <div className="flex flex-col items-center gap-1.5">
        <Button variant="ghost" size="sm" onClick={handleStopWaiting} className="gap-1.5" data-testid="stop-waiting">
          <Home className="size-3.5" aria-hidden />
          不等了，先回首页
        </Button>
        <p className="text-center text-xs text-muted-foreground">
          转写会继续跑，回首页「最近转写」还能找回来
        </p>
      </div>

      {process.env.NEXT_PUBLIC_USE_MOCK !== 'false' ? (
        <p className="text-center text-xs text-muted-foreground">
          演示模式：转写全程约 10 秒，完成后自动进入结果页
        </p>
      ) : null}
    </div>
  )
}

export function ProcessingSkeleton() {
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-4 py-8 sm:py-12">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <Skeleton className="h-24 w-full rounded-xl sm:w-44" />
        <div className="flex flex-1 flex-col gap-2">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-5 w-full" />
          <Skeleton className="h-3 w-36" />
        </div>
      </div>
      <Card>
        <CardContent className="flex flex-col gap-6 p-6">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-px w-full" />
          <div className="flex flex-col gap-5">
            {[0, 1, 2, 3].map((index) => (
              <div key={index} className="flex items-center gap-3">
                <Skeleton className="size-8 rounded-full" />
                <Skeleton className="h-4 w-28" />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
