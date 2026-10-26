'use client'

import { useEffect, useState } from 'react'

/**
 * 限流冷却倒计时。
 *
 * 时长优先取服务端 429 响应里的 `Retry-After`：后端是令牌桶，按
 * `per_minute/60` 每秒回填，真正要等多久取决于当时配额，前端算不出来。
 * 写死 60 秒会让用户白等——默认 10 次/分钟时 6 秒就够。
 *
 * 兜底值仍保留 60 秒：只在响应缺少该头时用（老后端、代理吞头、或错误来自
 * 缓存/离线场景），宁可多等也不要「冷却结束点一下必然再 429」。
 *
 * 抽成 hook 的原因：整页错误卡（ErrorAlert）和总结区错误卡（SummaryErrorState）
 * 是两条独立的失败路径，但「429 之后该等多久、等待期内能不能点重试」是同一件事，
 * 两处各写一遍迟早会漂移。
 */
export const RATE_LIMIT_COOLDOWN_SECONDS = 60

/** 冷却中返回剩余秒数（>0），非限流或冷却结束返回 0 */
export function useRateLimitCooldown(rateLimited: boolean, retryAfter?: number): number {
  const [cooldown, setCooldown] = useState(0)

  useEffect(() => {
    if (!rateLimited) {
      setCooldown(0)
      return
    }
    // 服务端给的值合法就用它（>=1），否则退回默认值
    const total =
      typeof retryAfter === 'number' && Number.isFinite(retryAfter) && retryAfter >= 1
        ? Math.ceil(retryAfter)
        : RATE_LIMIT_COOLDOWN_SECONDS
    setCooldown(total)
    const timer = window.setInterval(() => {
      setCooldown((value) => (value <= 1 ? 0 : value - 1))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [rateLimited, retryAfter])

  return cooldown
}
