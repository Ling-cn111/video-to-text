/**
 * 轮询节奏（P1-8）纯函数部分。
 *
 * 旧实现固定 800ms。对「Mock 10 秒跑完」很合适，对真实任务很不合适：
 * 后端本地 ASR 跑 150 秒音频要 40-155 秒，长视频更久 —— 这期间会打近 200 次 GET。
 * 轮询不限流（后端只拦 POST），所以不会 429，但会白白消耗 Serverless 调用次数，
 * 也让移动端一直醒着耗电。
 *
 * 策略：按**当前阶段已持续时长**退避 —— 阶段刚变时 800ms（用户此刻最想看到
 * 进度动起来），10 秒后退到 3 秒。阶段一变立刻回到快档。
 * 这里只放纯函数，钩子见 `use-poll-interval.ts`。
 */

/** 快档：阶段刚变、刚开始跑 */
export const FAST_MS = 800
/** 慢档：长时间无阶段变化的稳态 */
export const SLOW_MS = 3_000
/** 保持快档的时长（毫秒）：足够看到 parse_link → extract_audio 的跳变 */
export const FAST_WINDOW_MS = 10_000

/** 当前应使用的轮询间隔 */
export function pollIntervalFor(stageElapsedMs: number): number {
  return stageElapsedMs < FAST_WINDOW_MS ? FAST_MS : SLOW_MS
}

/** 距离切到慢档还剩多久（毫秒）；已过窗口返回 0 */
export function msUntilSlow(stageElapsedMs: number): number {
  return Math.max(0, FAST_WINDOW_MS - stageElapsedMs)
}
