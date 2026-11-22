import type { Summary } from '@/lib/types'

/**
 * 重点帧时间点派生（PR #89）：从 AI 总结的章节起点 + 要点时间合并出抽帧锚点。
 *
 * 与后端 services/frames.py 的 sanitize_timestamps 保持同一口径：
 * - 非法值（NaN/负数）丢弃
 * - duration > 1 时钳到 duration - 1（末尾 1s 的帧可能黑屏/解码失败）
 * - 升序排序后按 2s 近邻合并（更近的画面几乎一样）
 * - 截断到 FRAME_MAX_COUNT（与后端 FRAMES_MAX_COUNT 默认一致）
 *
 * 服务端仍会再净化一遍：前端不必追求精确，这里只保证「请求体本身就是干净集合」。
 */

export const FRAME_MIN_GAP_SECONDS = 2
export const FRAME_MAX_COUNT = 12

export function deriveFrameTimestamps(summary: Summary | null, duration: number): number[] {
  if (!summary) return []
  const candidates: number[] = []
  for (const chapter of summary.chapters ?? []) {
    candidates.push(chapter.timeStart)
  }
  for (const point of summary.keyPoints ?? []) {
    candidates.push(point.time)
  }

  const clampMax = Number.isFinite(duration) && duration > 1 ? duration - 1 : Number.POSITIVE_INFINITY
  const cleaned = candidates
    .filter((value) => Number.isFinite(value) && value >= 0)
    .map((value) => Math.min(value, clampMax))
    .map((value) => Math.round(value * 100) / 100)
    .sort((a, b) => a - b)

  const deduped: number[] = []
  for (const point of cleaned) {
    if (deduped.length > 0 && point - deduped[deduped.length - 1] < FRAME_MIN_GAP_SECONDS) {
      continue
    }
    deduped.push(point)
  }
  return deduped.slice(0, FRAME_MAX_COUNT)
}
