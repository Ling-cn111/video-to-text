'use client'

import { useEffect, useState } from 'react'

/**
 * 限流冷却倒计时。
 *
 * 后端 `RATE_LIMIT_PER_MINUTE` 默认 10，令牌桶按 per_minute/60 每秒回填，
 * 撞限后最多 60 秒就能重新攒满一个令牌。前端倒计时只是给用户一个可预期的等待，
 * 不做真实配额计算——配额状态只有后端知道，前端算出来也不会更准。
 *
 * 抽成 hook 的原因：整页错误卡（ErrorAlert）和总结区错误卡（SummaryErrorState）
 * 是两条独立的失败路径，但「429 之后该等多久、等待期内能不能点重试」是同一件事，
 * 两处各写一遍迟早会漂移。
 */
export const RATE_LIMIT_COOLDOWN_SECONDS = 60

/** 冷却中返回剩余秒数（>0），非限流或冷却结束返回 0 */
export function useRateLimitCooldown(rateLimited: boolean): number {
  const [cooldown, setCooldown] = useState(0)

  useEffect(() => {
    if (!rateLimited) {
      setCooldown(0)
      return
    }
    setCooldown(RATE_LIMIT_COOLDOWN_SECONDS)
    const timer = window.setInterval(() => {
      setCooldown((value) => (value <= 1 ? 0 : value - 1))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [rateLimited])

  return cooldown
}
