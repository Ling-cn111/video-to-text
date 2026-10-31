import { extractFirstUrl, normalizeVideoInput, resolveBareVideoId } from '@/lib/platform'

/**
 * 「从别的 App 分享过来」的文本 → 可以直接交给 `submitUrl` 的输入。
 *
 * ## 为什么需要这一层
 *
 * B站 App 的分享按钮给出的不是链接，而是一整段：
 *
 * ```
 * 【标题】 看看这个 https://b23.tv/AbCdEfG 复制打开
 * ```
 *
 * 有时甚至连链接都没有，只有用户从评论区复制的裸号。这一层负责把这两种
 * 形态都归一到「前端能解析、后端能接受」的输入；认不出就返回 null，
 * 由调用方给用户一句明确的提示，而不是把整段文案丢进 `new URL` 里。
 *
 * 纯函数、不碰原生 API：Node 环境的 E2E 可以直接 import 它跑断言。
 */
export function sharedTextToVideoInput(text: string | null | undefined): string | null {
  const trimmed = (text ?? '').trim()
  if (!trimmed) return null

  const url = extractFirstUrl(trimmed)
  if (url) return normalizeVideoInput(url)

  // 没有链接：整段可能只是用户复制的裸 BV / av 号。
  // 只认 resolveBareVideoId 认得出来的形态，不做模糊猜测（纯数字太容易误判）。
  if (resolveBareVideoId(trimmed)) return normalizeVideoInput(trimmed)

  return null
}

/** 原生侧送过来的分享内容（见 `ShareIntentPlugin.java` 的 payload） */
export interface SharePayload {
  text?: string
  subject?: string
}

/**
 * 同一条分享被处理两次的拦截窗口。
 *
 * 为什么需要：分享事件是 retain 的，JS 首次注册监听器时会补发一次；
 * 如果此时 React 的 effect 因为 StrictMode 或路由切换重建了监听器，
 * 补发 + 实时派发可能落进两个不同的监听器，于是用户看到「解析了两次」。
 * 用「同文本 + 短时间窗」去重，比在原生侧维护状态简单得多。
 */
export const SHARE_DEDUPE_MS = 5000

/**
 * 判断这次分享是否应当被忽略（重复投递）。
 * `last` 由调用方持有（模块级变量或 ref），返回新的 last 值。
 */
export function shouldIgnoreShare(
  payload: SharePayload | null | undefined,
  last: { text: string; at: number } | null,
  now: number,
): boolean {
  const text = (payload?.text ?? '').trim()
  if (!text) return true
  if (!last) return false
  if (last.text !== text) return false
  return now - last.at < SHARE_DEDUPE_MS
}
