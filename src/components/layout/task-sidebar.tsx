'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { FileText, Loader2, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { PlatformBadge } from '@/components/shared/platform-badge'
import { processingHref, resultHref } from '@/lib/routes'
import {
  RECENT_TASKS_CHANGED,
  formatRelativeTime,
  readRecentTasks,
  removeRecentTasks,
  type RecentTask,
} from '@/lib/recent-tasks'

/**
 * 桌面任务侧栏（PR18，lg+ 全局）：最近转写列表 + 勾选批量删除。
 *
 * 布局与不变量（docs/frontend-features.md §22）：
 * - 仅 `lg+` 渲染；`<lg`（含移动壳）不出现，首页保留内嵌「最近转写」区块（现状不变）
 * - 数据源与首页区块同一份 localStorage（recentTasks，最多 8 条，只存元数据）
 * - 条目点击 = 跳转（processing → 进度页 / done → 结果页）；复选框勾选与跳转互不干扰
 * - 「删除所选」批量删除；空列表整栏不渲染
 * - 跨页刷新：监听 RECENT_TASKS_CHANGED 事件（任务完成/删除/新增时由写函数派发）
 */
export function TaskSidebar() {
  const router = useRouter()
  const [tasks, setTasks] = useState<RecentTask[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const refresh = useCallback(() => setTasks(readRecentTasks()), [])

  useEffect(() => {
    refresh()
    const onChanged = () => refresh()
    window.addEventListener(RECENT_TASKS_CHANGED, onChanged)
    window.addEventListener('storage', onChanged)
    return () => {
      window.removeEventListener(RECENT_TASKS_CHANGED, onChanged)
      window.removeEventListener('storage', onChanged)
    }
  }, [refresh])

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function handleDeleteSelected() {
    if (selected.size === 0) return
    removeRecentTasks(Array.from(selected))
    setSelected(new Set())
    refresh()
    toast.success(`已删除 ${selected.size} 条记录`)
  }

  if (tasks.length === 0) return null

  return (
    <aside
      className="sticky top-14 hidden max-h-[calc(100vh-4.5rem)] w-64 shrink-0 flex-col gap-2 overflow-y-auto border-r px-3 py-6 lg:flex"
      data-testid="task-sidebar"
      aria-label="最近转写列表"
    >
      <div className="flex items-center justify-between px-1">
        <span className="text-sm font-semibold text-muted-foreground">最近转写</span>
        {selected.size > 0 ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 gap-1 px-2 text-xs text-destructive hover:text-destructive"
            onClick={handleDeleteSelected}
            data-testid="task-sidebar-delete"
          >
            <Trash2 className="size-3.5" aria-hidden />
            删除所选（{selected.size}）
          </Button>
        ) : null}
      </div>

      <ul className="space-y-1">
        {tasks.map((task) => {
          const isSelected = selected.has(task.taskId)
          return (
            <li
              key={task.taskId}
              className={`flex items-start gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-muted/60 ${isSelected ? 'bg-muted/60' : ''}`}
              data-testid="task-sidebar-item"
            >
              {/* 原生 checkbox（零新依赖）：stopPropagation 防止触发行跳转 */}
              <input
                type="checkbox"
                checked={isSelected}
                onChange={() => toggle(task.taskId)}
                onClick={(event) => event.stopPropagation()}
                aria-label={`选择 ${task.title}`}
                className="mt-1 size-4 shrink-0 cursor-pointer accent-primary"
                data-testid={`task-sidebar-check-${task.taskId}`}
              />
              <button
                type="button"
                className="min-w-0 flex-1 text-left"
                onClick={() =>
                  router.push(task.status === 'processing' ? processingHref(task.taskId) : resultHref(task.taskId))
                }
                data-testid={`task-sidebar-item-${task.taskId}`}
              >
                <div className="flex items-center gap-1.5">
                  {task.status === 'processing' ? (
                    <Loader2 className="size-3.5 shrink-0 animate-spin text-primary" aria-hidden />
                  ) : (
                    <FileText className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  )}
                  <span className="truncate text-sm">{task.title}</span>
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                  <PlatformBadge platform={task.platform} className="scale-90" />
                  <span>{formatRelativeTime(task.savedAt)}</span>
                  <span>{task.status === 'processing' ? '进行中' : '已完成'}</span>
                </div>
              </button>
            </li>
          )
        })}
      </ul>
      <p className="px-1 text-[11px] leading-relaxed text-muted-foreground">
        本机记录，换设备看不到；只保留最近 8 条。
      </p>
    </aside>
  )
}
