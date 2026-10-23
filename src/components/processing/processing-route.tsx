'use client'

import { useSearchParams } from 'next/navigation'
import { ProcessingView } from '@/components/processing/processing-view'
import { MissingTask } from '@/components/shared/missing-task'

/**
 * /processing?id=<taskId> 的客户端入口（静态导出形态）。
 *
 * 动态段 /processing/[taskId] 在桌面端的静态导出里无法枚举 taskId，所以内部导航
 * 一律用查询参数（见 lib/routes.ts）；web 端同时保留动态段路由以兼容旧链接。
 */
export function ProcessingRoute() {
  const taskId = useSearchParams().get('id')
  if (!taskId) return <MissingTask title="缺少任务参数" />
  return <ProcessingView taskId={taskId} />
}
