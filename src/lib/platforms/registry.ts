import type { Platform } from '@/lib/types'

/**
 * 平台注册表：新增平台只需在此追加条目（M2 多平台支持，见 docs/ROADMAP.md）。
 * UI 侧（平台徽章、封面配色）均由注册表驱动，不需要改组件。
 *
 * 注意区分两件事：
 * - 注册表 = 前端**认得**的域名（用于识别链接属于哪个平台、怎么取 videoId）
 * - `SUPPORTED_PLATFORMS` = 本构建**真的能转写**的平台（首页可以对外宣称的范围）
 * 两者不是一回事，见下方 SUPPORTED_PLATFORMS 的说明。
 */
export interface PlatformMeta {
  id: Platform
  name: string
  /** 口语简称，用于首页标题与输入框 placeholder（如「B站 / 抖音」） */
  shortName: string
  /** 展示用主色（徽章文字、图标描边） */
  color: string
  /** 封面占位渐变（未提供封面图时的兜底） */
  gradient: [string, string]
  /** 正式站域名（www / m 等子域均可） */
  hostPatterns: RegExp[]
  /** 分享短链域名，仅用于识别平台；真实后端会 302 展开短链 */
  shareHostPatterns: RegExp[]
  /** 从 URL 路径提取 videoId 的规则，取第 1 个捕获组 */
  videoIdPatterns: RegExp[]
}

export const PLATFORM_REGISTRY: Record<Platform, PlatformMeta> = {
  bilibili: {
    id: 'bilibili',
    name: '哔哩哔哩',
    shortName: 'B站',
    color: '#fb7299',
    gradient: ['#fb7299', '#ff9cb8'],
    hostPatterns: [/^([\w-]+\.)*bilibili\.com$/],
    shareHostPatterns: [/^b23\.tv$/],
    videoIdPatterns: [/\/video\/(BV[\w]+)/i, /\/video\/(av\d+)/i],
  },
  douyin: {
    id: 'douyin',
    name: '抖音',
    shortName: '抖音',
    color: '#fe2c55',
    gradient: ['#25f4ee', '#fe2c55'],
    hostPatterns: [/^([\w-]+\.)*douyin\.com$/],
    shareHostPatterns: [/^v\.douyin\.com$/],
    videoIdPatterns: [/\/video\/(\d+)/],
  },
}

/**
 * 本构建真正能转写的平台 —— 首页文案、输入校验、示例链接的唯一事实来源。
 *
 * 为什么不直接拿全量注册表当宣称：后端 `backend/app/platforms.py` 只注册了 bilibili，
 * 抖音仅存在于前端 Mock 的演示数据（`src/lib/mock-data.ts` 的 DOUYIN_DATA）里。
 * 此前首页照着注册表写死「支持 B站 / 抖音」，用户粘贴抖音链接后才被后端
 * 422 UNSUPPORTED_PLATFORM 拒绝 —— 预期违背比功能缺失更伤信任，这类文案必须
 * 跟着实际能力走，而不是跟着「我们认得哪些域名」走。
 *
 * Mock 模式确实能演示抖音，所以两者都列；真实模式只能承诺 B站。
 */
const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK !== 'false'

export const SUPPORTED_PLATFORMS: readonly Platform[] = USE_MOCK
  ? (['bilibili', 'douyin'] as const)
  : (['bilibili'] as const)

export function isPlatformSupported(platform: Platform): boolean {
  return SUPPORTED_PLATFORMS.includes(platform)
}

/** 支持平台的正式名，用于错误提示：「哔哩哔哩、抖音」 */
export function supportedPlatformsText(separator = '、'): string {
  return SUPPORTED_PLATFORMS.map((platform) => PLATFORM_REGISTRY[platform].name).join(separator)
}

/** 支持平台的简称，用于首页标题 / placeholder 这类紧凑位置：「B站 / 抖音」 */
export function supportedPlatformsShortText(separator = ' / '): string {
  return SUPPORTED_PLATFORMS.map((platform) => PLATFORM_REGISTRY[platform].shortName).join(separator)
}

export function getPlatformMeta(platform: Platform): PlatformMeta {
  return PLATFORM_REGISTRY[platform]
}

/**
 * 「不认识这个站」的提示。只承诺当前构建真能做的平台，避免又一处写死文案。
 */
export function unsupportedHostMessage(host?: string): string {
  const supported = supportedPlatformsText()
  return host ? `暂不支持「${host}」平台，当前支持${supported}` : `暂不支持该平台，当前支持${supported}`
}

/**
 * 「认得这个站，但当前构建还没接上」的提示。
 *
 * 这是本次修的预期违背点：旧文案对所有情况都说「当前支持哔哩哔哩、抖音」，
 * 而真实后端只接了 B站 —— 用户粘贴抖音链接后一路走到后端才收到 422。
 * 现在拦在前端，并说清楚「还没支持」而不是「不支持」（后者听起来像永远不会支持）。
 */
export function unavailablePlatformMessage(platform: Platform): string {
  const meta = PLATFORM_REGISTRY[platform]
  return `${meta.name}视频暂未支持，当前可转写${supportedPlatformsText()}视频链接`
}
