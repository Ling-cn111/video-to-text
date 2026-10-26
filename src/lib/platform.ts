import {
  PLATFORM_REGISTRY,
  getPlatformMeta,
  isPlatformSupported,
} from '@/lib/platforms/registry'
import { isBlockedHost } from '@/lib/url-guard'
import type { Platform } from '@/lib/types'

export interface ResolveSuccess {
  ok: true
  platform: Platform
  videoId: string
}

/** 格式非法：无法 URL 化 / 非 http(s) / 命中 SSRF 黑名单 */
export interface ResolveInvalid {
  ok: false
  reason: 'invalid'
}

/** 域名根本不在平台注册表：我们不认识这个站 */
export interface ResolveUnsupported {
  ok: false
  reason: 'unsupported'
  /** 命中的域名，用于错误提示 */
  host?: string
}

/**
 * 域名认得，但当前构建（真实后端）还没接上这个平台。
 * 与 unsupported 分开是因为提示语完全不同 —— 「我们不认识这个站」和
 * 「这个站我们还没接上」对用户是两件事，后者要说清楚现在能转什么。
 */
export interface ResolveUnavailable {
  ok: false
  reason: 'unavailable'
  platform: Platform
  host?: string
}

export type ResolveFailure = ResolveInvalid | ResolveUnsupported | ResolveUnavailable

export type ResolveResult = ResolveSuccess | ResolveFailure

/**
 * 解析用户输入的视频链接。
 * - 格式非法（无法 URL 化、非 http(s)）→ { ok: false, reason: 'invalid' }
 * - 域名不在平台注册表 → { ok: false, reason: 'unsupported' }
 * - 域名认得但当前构建不支持该平台 → { ok: false, reason: 'unavailable' }
 * - 成功 → 平台 + videoId
 *
 * 注意：仅做字符串层面的解析，不发起任何网络请求（SSRF 防护交由真实后端完成）。
 */
export function resolveVideoUrl(rawUrl: string): ResolveResult {
  const trimmed = rawUrl.trim()
  let url: URL
  try {
    url = new URL(trimmed)
  } catch {
    return { ok: false, reason: 'invalid' }
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    return { ok: false, reason: 'invalid' }
  }
  // 拒绝 localhost / 环回 / 私有 / 保留地址（为接入真实后端预留的 SSRF 入口约束）
  if (isBlockedHost(url.hostname)) {
    return { ok: false, reason: 'invalid' }
  }

  const host = url.hostname.toLowerCase()
  for (const meta of Object.values(PLATFORM_REGISTRY)) {
    const isMainHost = meta.hostPatterns.some((pattern) => pattern.test(host))
    const isShareHost = meta.shareHostPatterns.some((pattern) => pattern.test(host))
    if (!isMainHost && !isShareHost) continue

    // 认得这个域名，但当前构建（真实后端）还没接上：拦在本地，别让用户等一轮 422
    if (!isPlatformSupported(meta.id)) {
      return { ok: false, reason: 'unavailable', platform: meta.id, host }
    }

    // 路径里能直接取到 videoId（B站 b23.tv/BVxxx 短链路径即含 ID）
    for (const pattern of meta.videoIdPatterns) {
      const match = url.pathname.match(pattern)
      if (match?.[1]) return { ok: true, platform: meta.id, videoId: match[1] }
    }
    // 取不到 videoId：抖音 v.douyin.com 短链等场景，Demo 用末段路径做占位
    const segments = url.pathname.split('/').filter(Boolean)
    const videoId = segments.at(-1) ?? fallbackVideoId(url)
    return { ok: true, platform: meta.id, videoId }
  }

  return { ok: false, reason: 'unsupported', host }
}

function fallbackVideoId(url: URL): string {
  const digest = Array.from(url.pathname + url.search).reduce(
    (acc, ch) => (acc * 31 + ch.charCodeAt(0)) >>> 0,
    0,
  )
  return `demo${digest.toString(36)}`
}

export { getPlatformMeta }
