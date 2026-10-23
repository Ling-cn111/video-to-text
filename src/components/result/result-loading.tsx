import { ResultSkeleton } from '@/components/result/result-view'

/** Suspense 兜底：与结果页自身加载态一致 */
export function ResultLoading() {
  return <ResultSkeleton />
}
