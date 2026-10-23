import { ProcessingSkeleton } from '@/components/processing/processing-view'

/** Suspense 兜底：与进度页自身加载态一致，避免切换时闪不同骨架 */
export function ProcessingLoading() {
  return <ProcessingSkeleton />
}
