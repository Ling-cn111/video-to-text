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
 * 从一段混杂文本里抽出第一个 http(s) 链接。
 *
 * ## 为什么必须有它
 *
 * 用户几乎从不「复制一个干净的链接」。B站 App 的分享按钮给出的是一整段：
 *
 * ```
 * 【标题】 看看这个 https://b23.tv/AbCdEfG 复制打开
 * ```
 *
 * 直接 `new URL(整段)` 会失败（有空格），或者更糟——静默成功但把尾随文字
 * 百分号编码进 path（`BV1xx411c7mD%20%E5%A4%8D%E5%88%B6...`），
 * 于是用户拿到一个「视频不存在」。这是深链/分享意图的前置能力：
 * 原生侧送进来的永远是整段分享文案。
 *
 * ## 为什么用「逐字符扫描」而不是一个宽松正则
 *
 * URL 里合法但中文文案里常见的字符太多了（`）`、`】`、`，`）。一个
 * `\S+` 的正则会把后面的中文一起吞掉。这里显式在遇到空白、中文、
 * 成对括号引号时截断，比事后清理更不容易出错。
 */
export function extractFirstUrl(text: string): string | null {
  if (!text) return null
  const start = text.search(/https?:\/\//i)
  if (start === -1) return null

  let end = start
  for (let i = start; i < text.length; i += 1) {
    const ch = text[i]
    if (isUrlTerminator(ch)) break
    end = i + 1
  }

  // 去掉 URL 末尾的标点：`https://b23.tv/abc。` 里的句号不属于链接
  let candidate = text.slice(start, end)
  while (candidate.length > 0 && TRAILING_PUNCTUATION.has(candidate[candidate.length - 1])) {
    candidate = candidate.slice(0, -1)
  }
  return candidate.length > 0 ? candidate : null
}

/** 出现在链接里就说明链接已经结束的字符 */
function isUrlTerminator(ch: string): boolean {
  if (/\s/.test(ch)) return true
  // 中文与全角标点：URL 里不可能出现（会先被百分号编码）
  if (/[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]/.test(ch)) return true
  return URL_TERMINATORS.has(ch)
}

const URL_TERMINATORS = new Set(['<', '>', '"', "'", '`', '“', '”', '‘', '’', '（', '）', '(', ')', '【', '】', '「', '」'])

const TRAILING_PUNCTUATION = new Set(['.', ',', ';', ':', '!', '?', '。', '，', '、', '；', '：', '！', '？'])

/**
 * 从一段文案里抽出**所有** http(s) 链接（按出现顺序）。
 *
 * 分享文案里不止一条链接：B站 的分享会带上「UP 主主页」「活动页」，
 * 有些机型还会把视频链接与「打开客户端」的落地页一起塞进来。只取第一个
 * 会抽到一条跟视频无关的链接，然后后端一路解析失败，用户看到的是
 * 「视频不存在或链接有误」——而链接其实没抄错。
 */
export function extractAllUrls(text: string): string[] {
  if (!text) return []
  const urls: string[] = []
  let from = 0
  while (from < text.length) {
    const start = text.slice(from).search(/https?:\/\//i)
    if (start === -1) break
    const absolute = from + start
    let end = absolute
    for (let i = absolute; i < text.length; i += 1) {
      if (isUrlTerminator(text[i])) break
      end = i + 1
    }
    let candidate = text.slice(absolute, end)
    while (candidate.length > 0 && TRAILING_PUNCTUATION.has(candidate[candidate.length - 1])) {
      candidate = candidate.slice(0, -1)
    }
    if (candidate.length > 0) urls.push(candidate)
    from = end > absolute ? end : absolute + 1
  }
  return urls
}

/**
 * 从混杂文本里挑出**最像视频链接**的那一条。
 *
 * 优先级（从高到低）：
 * 1. 路径能直接命中平台的 `videoIdPatterns`（`/video/BV…`、`/video/av…`）——
 *    这是真正的视频页，最该被选中；
 * 2. 域名命中平台（含短链域名）—— 认得是这个站，但可能是主页 / 活动页；
 * 3. 第一条 http(s) 链接 —— 一条都不认得时也返回，交给后续
 *    `unsupported` / `unavailable` 分支给出准确提示。不能因为「不像视频」
 *    就静默丢掉，那样用户连报错都看不到。
 *
 * ## 为什么第 1 档必须排在域名匹配之前
 *
 * `space.bilibili.com/123` 与 `www.bilibili.com/video/BV…` 都会命中
 * `bilibili.com` 的 `hostPatterns`。只按域名挑的话，遇到「UP 主主页 + 视频页」
 * 这种分享文案，抽到的是主页 —— 后端解析主页当然失败，用户看到的是
 * 「视频不存在或链接有误」，而他把链接抄得一字不差。
 */
export function extractVideoUrl(text: string): string | null {
  const urls = extractAllUrls(text)
  if (urls.length === 0) return null

  const parse = (candidate: string): URL | null => {
    try {
      return new URL(candidate)
    } catch {
      return null
    }
  }

  // 第 1 档：路径里能直接取到 videoId
  for (const candidate of urls) {
    const url = parse(candidate)
    if (!url) continue
    const hit = Object.values(PLATFORM_REGISTRY).some((meta) =>
      meta.videoIdPatterns.some((pattern) => pattern.test(url.pathname)),
    )
    if (hit) return candidate
  }

  // 第 2 档：域名属于某个平台（含 b23.tv 这类分享短链）
  for (const candidate of urls) {
    const url = parse(candidate)
    if (!url) continue
    const host = url.hostname.toLowerCase()
    const hit = Object.values(PLATFORM_REGISTRY).some(
      (meta) =>
        meta.hostPatterns.some((pattern) => pattern.test(host)) ||
        meta.shareHostPatterns.some((pattern) => pattern.test(host)),
    )
    if (hit) return candidate
  }

  // 第 3 档：随便哪条，让后端/用户拿到真实反馈
  return urls[0]
}

/**
 * 解析用户输入的视频链接。
 * - 裸 AV/BV 号（`BV1xx411c7mD` / `av170001`）→ 补成 B站 链接再走正常流程
 * - 混杂在分享文案里的链接（`【标题】 https://b23.tv/xxx 复制打开`）→ 先抽链接
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

  // 分享文案里夹着链接是常态。必须先抽出来再解析：整段 `new URL` 要么抛错，
  // 要么（更糟）静默把尾随文字编码进 path，得到一个不存在的 videoId。
  // 用 extractVideoUrl 而不是 extractFirstUrl：文案里可能有多条链接，
  // 固定取第一条会抽到 UP 主主页之类跟视频无关的地址。
  const candidate = extractVideoUrl(trimmed) ?? trimmed

  let url: URL
  try {
    url = new URL(candidate)
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

/**
 * 由已解析的 `VideoInfo` 重建规范链接。
 *
 * ## 为什么需要它
 *
 * B站 App 分享出来的是 `https://b23.tv/AbCdEfG` 这类短链。后端有两处快路径
 * 只认 `bilibili.com` 域名：
 * - `backend/app/services/subtitles.py:293-301` 的 `_extract_bvid`
 * - `backend/app/services/audio.py:138-141` 的 `_extract_bvid`
 *
 * 短链命中不了它们，于是字幕直取与 playurl 音频直取双双失效，退回
 * yt-dlp 下载 + ASR —— 结果一样，但慢很多。既然前端已经从链接里解析出了
 * videoId，用 videoId 重建 canonical 链接送后端，两条快路径就都活了。
 *
 * 认不出模板（未来的非 B站 平台）时返回 null，调用方保留原始链接。
 */
export function canonicalUrlFor(video: { platform: Platform; videoId: string }): string | null {
  if (video.platform !== 'bilibili') return null
  // av 号同样可以走 /video/av<digits>，B站 会自己 302 到 BV 页
  if (!/^(BV[0-9A-Za-z]+|av\d+)$/.test(video.videoId)) return null
  return `https://www.bilibili.com/video/${video.videoId}`
}

export { getPlatformMeta }
