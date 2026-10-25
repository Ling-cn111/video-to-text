'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Clock3, FileText, Loader2 } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { PlatformBadge } from '@/components/shared/platform-badge'
import { formatDuration } from '@/lib/format'
import { processingHref, resultHref } from '@/lib/routes'
import { formatRelativeTime, readRecentTasks, type RecentTask } from '@/lib/recent-tasks'

/**
 * 首页「最近转写」（P1-7）。
 *
 * 挂载后才读 localStorage：SSR 阶段读不到，直接渲染会让首屏 HTML 与服务端不一致
 * （hydration mismatch）。空列表时整块不渲染，首页保持「一个输入框就能用」的干净入口。
 */
export function RecentTasks() {
  const router = useRouter()
  const [tasks, setTasks] = useState<RecentTask[]>([])

  useEffect(() => {
    setTasks(readRecentTasks())
  }, [])

  if (tasks.length === 0) return null

  const open = (task: RecentTask) => {
    router.push(task.status === 'processing' ? processingHref(task.taskId) : resultHref(task.taskId))
  }

  return (
    <section className="flex flex-col gap-2" data-testid="recent-tasks">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-medium text-muted-foreground">最近转写</h2>
        <span className="text-xs text-muted-foreground">本机记录，换设备看不到</span>
      </div>
      <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {tasks.map((task) => (
          <li key={task.taskId}>
            <Card className="rounded-xl">
              <CardContent className="p-0">
                <button
                  type="button"
                  onClick={() => open(task)}
                  data-testid="recent-task-item"
                  className="flex w-full items-center gap-3 rounded-xl p-2.5 text-left transition-colors hover:bg-accent"
                >
                  <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                    {task.status === 'processing' ? (
                      <Loader2 className="size-4 animate-spin" aria-hidden />
                    ) : (
                      <FileText className="size-4" aria-hidden />
                    )}
                  </span>
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="truncate text-sm font-medium">{task.title}</span>
                    <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                      <PlatformBadge platform={task.platform} />
                      <span className="inline-flex items-center gap-1 tabular-nums">
                        <Clock3 className="size-3" aria-hidden />
                        {formatDuration(task.duration)}
                      </span>
                      <span>{formatRelativeTime(task.savedAt)}</span>
                      <span className="text-primary">
                        {task.status === 'processing' ? '查看进度' : '查看结果'}
                      </span>
                    </span>
                  </span>
                </button>
              </CardContent>
            </Card>
          </li>
        ))}
      </ul>
    </section>
  )
}
