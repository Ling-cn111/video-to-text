import { compareVersions, parseVersion } from '@/lib/updater'

/**
 * 移动端「检查更新」。
 *
 * ## 为什么移动端不能复用桌面端的 updater
 *
 * Tauri updater 是 Rust 侧的实现，Capacitor 壳里没有它（`tauri.conf.json` 的
 * `plugins.updater` 在 APK 里根本不存在）。Android 的升级路径也不同：装一个新
 * APK 覆盖旧版，不需要增量下载与签名校验那一套。
 *
 * ## 为什么不静默下载 APK
 *
 * Android 不允许应用自己静默安装——必须由系统安装器接管，用户手动点「安装」。
 * 所以这里的职责只有一件：**告诉用户有没有新版，并把他送到下载页**。
 *
 * ## 为什么用 api.github.com 而不是 latest.json
 *
 * `latest.json` 是给 Tauri updater 用的，字段里没有 APK 的下载地址，
 * 而且它是 releases/latest/download 重定向后的裸文件，CORS 头不可控。
 * `api.github.com/repos/.../releases/latest` 明确返回
 * `Access-Control-Allow-Origin: *`（已实测），WebView 可直接 fetch。
 *
 * 未认证请求限流 60 次/小时/IP，对「手动点检查」这个频次完全够用。
 */

/** GitHub 最新 Release 的 API（CORS 允许跨域，已实测） */
export const LATEST_RELEASE_API =
  'https://api.github.com/repos/Ling-cn111/video-to-text/releases/latest'

/** 移动端下载页：检查失败或发现新版时的出口 */
export const ANDROID_RELEASES_PAGE = 'https://github.com/Ling-cn111/video-to-text/releases/latest'

/** 移动端检查超时（毫秒）。移动网络下 GitHub 可能很慢 */
export const MOBILE_CHECK_TIMEOUT_MS = 12000

/** 当前运行的版本（构建期由 next.config.mjs 从 package.json 注入） */
export function currentAppVersion(): string {
  return process.env.NEXT_PUBLIC_APP_VERSION ?? '0.0.0'
}

export interface LatestRelease {
  /** tag 名，例如 v0.1.6 */
  tag: string
  /** 去掉前缀 v 的版本号 */
  version: string
  /** 发布页地址 */
  pageUrl: string
  /** APK 直链；该 Release 没传 APK 时为 null */
  apkUrl: string | null
}

export type MobileUpdateResult =
  | { kind: 'up-to-date'; version: string }
  | { kind: 'available'; release: LatestRelease; currentVersion: string }
  | { kind: 'error'; message: string }

/**
 * 从 GitHub Release 的 JSON 里提取需要的信息。
 *
 * 单独拆成纯函数是为了能在 Node 里单测——真实请求在 E2E 里不可能稳定复现
 * （GitHub 限流、网络、Release 内容都会变）。
 */
export function parseLatestRelease(payload: unknown): LatestRelease | null {
  if (!payload || typeof payload !== 'object') return null
  const record = payload as Record<string, unknown>

  const tag = typeof record.tag_name === 'string' ? record.tag_name : ''
  if (!tag) return null
  // tag 带 v 前缀（本项目的发版惯例），parseVersion 只认数字开头，先去前缀再解析
  const version = tag.replace(/^v/i, '')
  // 版本号解析失败说明 tag 不是版本格式（例如 latest），此时宁可当作没有新版，
  // 也不要弹一个装不上的提示
  if (!parseVersion(version)) return null

  const pageUrl = typeof record.html_url === 'string' ? record.html_url : ANDROID_RELEASES_PAGE

  // 找 APK 资产：文件名以 .apk 结尾即可，不硬编码版本号
  let apkUrl: string | null = null
  const assets = Array.isArray(record.assets) ? record.assets : []
  for (const asset of assets) {
    if (!asset || typeof asset !== 'object') continue
    const item = asset as Record<string, unknown>
    const name = typeof item.name === 'string' ? item.name : ''
    const url = typeof item.browser_download_url === 'string' ? item.browser_download_url : ''
    if (name.toLowerCase().endsWith('.apk') && url) {
      apkUrl = url
      break
    }
  }

  return { tag, version, pageUrl, apkUrl }
}

/**
 * 判断这个 Release 是否比当前版本新。
 *
 * 与桌面端共用 `compareVersions`：版本号比较的规则只能有一份，
 * 两处各写一套必然在某次改动后分叉（`0.1.10` vs `0.1.9` 是经典陷阱）。
 */
export function isNewerRelease(release: LatestRelease, currentVersion: string): boolean {
  return compareVersions(release.version, currentVersion) > 0
}

/**
 * 查询是否有新版 APK。
 *
 * `fetchImpl` 可注入，便于测试；默认用全局 fetch。
 */
export async function checkMobileUpdate(
  currentVersion: string,
  fetchImpl: typeof fetch = fetch,
): Promise<MobileUpdateResult> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), MOBILE_CHECK_TIMEOUT_MS)

  try {
    const response = await fetchImpl(LATEST_RELEASE_API, {
      headers: {
        // GitHub API 要求带 UA，否则部分情况下返回 403
        Accept: 'application/vnd.github+json',
      },
      signal: controller.signal,
      // 不要缓存：用户点「检查更新」期待的是此刻的真实状态
      cache: 'no-store',
    })

    if (!response.ok) {
      return { kind: 'error', message: `更新服务器返回 ${response.status}` }
    }

    const release = parseLatestRelease(await response.json())
    if (!release) {
      return { kind: 'error', message: '没能解析更新信息' }
    }

    if (!isNewerRelease(release, currentVersion)) {
      return { kind: 'up-to-date', version: currentVersion }
    }
    return { kind: 'available', release, currentVersion }
  } catch (error) {
    // AbortError 与网络错误都归到这里：对用户而言都是「连不上」
    const aborted = error instanceof Error && error.name === 'AbortError'
    return { kind: 'error', message: aborted ? '连接更新服务器超时' : '连接更新服务器失败' }
  } finally {
    clearTimeout(timer)
  }
}
