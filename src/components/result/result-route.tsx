'use client'

import { useSearchParams } from 'next/navigation'
import { ResultView } from '@/components/result/result-view'
import { MissingTask } from '@/components/shared/missing-task'

/** /result?id=<taskId> 的客户端入口（静态导出形态），说明见 processing-route.tsx */
export function ResultRoute() {
  const taskId = useSearchParams().get('id')
  if (!taskId) return <MissingTask title="缺少任务参数" />
  return <ResultView taskId={taskId} />
}
