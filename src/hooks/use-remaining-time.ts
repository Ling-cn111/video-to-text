'use client'

import { useEffect, useRef, useState } from 'react'
import type { TaskStage } from '@/lib/types'

/**
 * 剩余时间预估（P1-8）。
 *
 * 为什么不用「按总进度线性外推」：进度是**分段锚定**的，不是匀速的 ——
 * parse_link 只占 5%，extract_audio 15-35%，asr 40-95%（见
 * `backend/app/services/transcribe.py` 的 PROGRESS_* 常量）。
 * 用 `progress / elapsed` 反推总时长，会在刚开始的 5% 上得出「还剩 8 秒」，
 * 然后卡两分钟不动 —— 比不显示更伤信任。
 *
 * 实际做法：**只估当前阶段**，用该阶段内的 Δ进度 / Δ时间 外推到阶段终点。
 * 阶段切换时重新采样，误差不跨阶段累积。因此文案说的是「本阶段预计还需」，
 * 而不是编一个看似精确的总时长。
 *
 * 三个保护：
 * - 观察窗口不足 MIN_WINDOW_MS 或进度没动 → 返回 null，UI 退回不显示
 * - 估计值只降不升（进度语义上不该往回跳，估计值回弹同样让人焦躁）
 * - ≤5 秒统一显示「即将完成」，避免 1-4 秒反复横跳
 */

/** 各阶段进度终点，与后端 PROGRESS_* 锚点一致 */
export const STAGE_END: Record<TaskStage, number> = {
  parse_link: 5,
  extract_audio: 35,
  asr: 95,
  summarize: 100,
}

/** 采样所需的最短观察窗口：太短会被网络抖动放大成荒谬的估计 */
export const MIN_WINDOW_MS = 3_000
/** 估计值上限：超过说明样本还不可靠，先不显示 */
export const MAX_ESTIMATE_SECONDS = 900

export interface StageSample {
  stage: TaskStage
  startProgress: number
  /** 采样起点时刻（毫秒时间戳） */
  startAt: number
}

/**
 * 纯函数：按当前阶段的采样算「这个阶段还剩几秒」。
 * 抽出来是为了能直接单测 —— 钩子里的时间逻辑不抽成纯函数就只能靠肉眼看。
 */
export function estimateStageRemaining(sample: StageSample, progress: number, now: number): number | null {
  const elapsed = now - sample.startAt
  const gained = progress - sample.startProgress
  if (elapsed < MIN_WINDOW_MS || gained <= 0) return null

  const perPercentMs = elapsed / gained
  const pointsLeft = Math.max(0, STAGE_END[sample.stage] - progress)
  const seconds = Math.round((pointsLeft * perPercentMs) / 1000)
  if (seconds > MAX_ESTIMATE_SECONDS) return null
  return seconds
}

/** 估计值只降不升：回弹比没有估计更让人焦躁 */
export function clampMonotonic(previous: number | null, next: number): number {
  return previous === null ? next : Math.min(previous, next)
}

export function useRemainingSeconds(
  stage: TaskStage | null,
  progress: number,
  active: boolean,
): number | null {
  const sampleRef = useRef<StageSample | null>(null)
  const floorRef = useRef<number | null>(null)
  const [remaining, setRemaining] = useState<number | null>(null)

  useEffect(() => {
    if (!active || !stage) {
      sampleRef.current = null
      floorRef.current = null
      setRemaining(null)
      return
    }

    const now = Date.now()
    const sample = sampleRef.current
    // 阶段变了（或首次采样）：重置基线，估计值也重新开始
    if (!sample || sample.stage !== stage) {
      sampleRef.current = { stage, startProgress: progress, startAt: now }
      floorRef.current = null
      setRemaining(null)
      return
    }

    const raw = estimateStageRemaining(sample, progress, now)
    if (raw === null) return
    const floor = clampMonotonic(floorRef.current, raw)
    floorRef.current = floor
    setRemaining(floor)
  }, [active, progress, stage])

  return remaining
}

/** 剩余秒数 → 文案。null 表示还没估出来（不显示） */
export function formatRemaining(seconds: number | null): string | null {
  if (seconds === null) return null
  if (seconds <= 5) return '即将完成'
  if (seconds < 60) return `本阶段预计还需约 ${seconds} 秒`
  return `本阶段预计还需约 ${Math.round(seconds / 60)} 分钟`
}
