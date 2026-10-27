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
 * 裸视频号（不带域名）的识别规则。
 *
 * 为什么需要它：用户在 B站 页面上复制到的常常只有 `BV1xx411c7mD` 这一段，
 * 或者从别人那里拿到 `av170001` 这样的号。要求他自己拼出完整 URL 是纯粹的
 * 摩擦——尤其是手机上，从 App 分享出来的往往就是这种短号。
 *
 * 只认 B站 的两种官方编号，不做模糊猜测：
 * - BV 号：`BV` + 10 位 base58 字符（现行长度；历史上的短 BV 号也兼容到 8 位以上）
 * - av 号：`av` + 纯数字
 * 别的平台（抖音 `7301234567890123456` 那种纯数字）不在此列——纯数字太容易
 * 与 av 号混淆，硬猜只会把用户送到错误的分支。
 */
const BARE_BV_PATTERN = /^bv([0-9a-z]{8,12})$/i
const BARE_AV_PATTERN = /^av(\d{1,12})$/i

/**
 * 把裸视频号规范成 (platform, videoId)；认不出返回 null。
 *
 * **只规范前缀的大小写，正文原样保留**：B站 BV 号是**大小写敏感**的
 * （`BV1xx411c7mD` 与 `BV1XX411C7MD` 是两个不同的号，后者根本不存在）。
 * 早先这里写 `bv[1].toUpperCase()`，会把用户复制的合法号悄悄改成另一个号，
 * 然后得到一个「视频不存在」——比不认更糟。用户把正文打错了我们也无从修正，
 * 只能原样发出去让服务商回答。
 *
 * av 号同理只统一 `av` 前缀的小写写法（数字部分没有大小写）。
 */
export function resolveBareVideoId(raw: string): { platform: Platform; videoId: string } | null {
  const trimmed = raw.trim()
  const bv = trimmed.match(BARE_BV_PATTERN)
  if (bv) return { platform: 'bilibili', videoId: `BV${bv[1]}` }
  const av = trimmed.match(BARE_AV_PATTERN)
  if (av) return { platform: 'bilibili', videoId: `av${av[1]}` }
  return null
}

/**
 * 把用户输入规范成「后端能接受的完整链接」。
 *
 * 裸 AV/BV 号在这里补成 `https://www.bilibili.com/video/<id>`：后端的
 * `parse_video_url` 第一步就是 `validate_public_http_url`，裸号必然被判非法。
 * 前端补全比让后端放宽校验更安全 —— 后者意味着后端要接受一种非 URL 的输入，
 * 那会削弱「只抓公网 http(s)」这条 SSRF 前提。
 *
 * 已经是完整链接的原样返回（只去首尾空白）。
 */
export function normalizeVideoInput(raw: string): string {
  const trimmed = raw.trim()
  const bare = resolveBareVideoId(trimmed)
  if (!bare) return trimmed
  const template = bare.platform === 'bilibili' ? 'https://www.bilibili.com/video/{id}' : null
  return template ? template.replace('{id}', bare.videoId) : trimmed
}

/**
 * 解析用户输入的视频链接。
 * - 裸 AV/BV 号（`BV1xx411c7mD` / `av170001`）→ 补成 B站 链接再走正常流程
 * - 格式非法（无法 URL 化、非 http(s)）→ { ok: false, reason: 'invalid' }
 * - 域名不在平台注册表 → { ok: false, reason: 'unsupported' }
 * - 域名认得但当前构建不支持该平台 → { ok: false, reason: 'unavailable' }
 * - 成功 → 平台 + videoId
 *
 * 注意：仅做字符串层面的解析，不发起任何网络请求（SSRF 防护交由真实后端完成）。
 */
export function resolveVideoUrl(rawUrl: string): ResolveResult {
  const trimmed = rawUrl.trim()
  // 先试裸视频号：它一定不是合法 URL，若放到 new URL 之后就只能报「链接无效」，
  // 而那正是用户最容易踩的坑（复制到的是号、不是链接）
  const bare = resolveBareVideoId(trimmed)
  if (bare) {
    if (!isPlatformSupported(bare.platform)) {
      return { ok: false, reason: 'unavailable', platform: bare.platform }
    }
    return { ok: true, platform: bare.platform, videoId: bare.videoId }
  }

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
