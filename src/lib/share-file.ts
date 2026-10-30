import { IS_MOBILE_SHELL } from '@/lib/runtime'

/**
 * 文件导出与分享：把生成好的文本交给系统。
 *
 * 三条路径，按可靠性从高到低（锁定项 §2「移动端导出」）：
 * 1. **Capacitor Share（移动端主路径）**：把内容写进应用缓存目录，拿到 `file://` URL
 *    后交给系统分享面板。Android WebView 里 `blob:` + `<a download>` 会变成新窗口
 *    或静默失败，所以壳内必须走原生插件。
 * 2. **`navigator.share({ files })`（网页 / PWA）**：手机浏览器上体验最好，
 *    但桌面 Chrome 与多数 Android WebView 不支持文件分享，故不是主路径。
 * 3. **`blob:` + `<a download>`（桌面兜底）**：桌面浏览器上最可靠，一直是它。
 *
 * 为什么不用 Web Share 当移动端主路径：Android WebView 的 Web Share API 依赖宿主
 * 实现，Capacitor 默认 WebView 上并不可靠（MetaMask 为此专门发过 patch）。
 *
 * 用户取消分享**不是错误**，必须与真实失败区分，否则每次取消都弹一个红条。
 */

/** 分享/下载的结果，调用方据此决定提示文案 */
export type ShareOutcome = 'shared' | 'downloaded' | 'cancelled' | 'failed'

export interface ShareFileInput {
  /** 含扩展名的文件名，例如 `某某视频-字幕.srt` */
  filename: string
  /** 文件内容（纯文本） */
  content: string
  /** MIME 类型，影响系统分享面板给出的可选项 */
  mime: string
  /** 分享面板标题（部分平台显示） */
  title?: string
}

/** 分享面板里没选目标、直接关掉时各平台抛出的错误特征 */
function isCancelError(error: unknown): boolean {
  if (!error) return false
  const name = (error as { name?: string }).name ?? ''
  const message = String((error as { message?: string }).message ?? error)
  return name === 'AbortError' || /cancel|取消|abort/i.test(message)
}

/** 路径 1：Capacitor 原生分享（仅移动端壳内启用） */
async function shareViaCapacitor(input: ShareFileInput): Promise<ShareOutcome> {
  const [{ Filesystem, Directory, Encoding }, { Share }] = await Promise.all([
    import('@capacitor/filesystem'),
    import('@capacitor/share'),
  ])

  // 写进 Cache 目录：系统分享面板只需要一个可读的 file:// URL，
  // 放 Cache 让系统在空间紧张时自行清理，不占用户存储。
  const { uri } = await Filesystem.writeFile({
    path: input.filename,
    data: input.content,
    directory: Directory.Cache,
    encoding: Encoding.UTF8,
  })

  await Share.share({ title: input.title ?? input.filename, files: [uri] })
  return 'shared'
}

/** 路径 2：Web Share API（需要浏览器支持分享文件） */
async function shareViaWebShare(input: ShareFileInput): Promise<ShareOutcome | null> {
  if (typeof navigator === 'undefined' || typeof navigator.share !== 'function') return null

  const file = new File([input.content], input.filename, { type: input.mime })
  // canShare 缺失时保守当作不支持：宁可走下载，也不要抛一个用户看不懂的错误
  if (typeof navigator.canShare === 'function' && !navigator.canShare({ files: [file] })) return null

  await navigator.share({ title: input.title ?? input.filename, files: [file] })
  return 'shared'
}

/** 路径 3：blob 下载（桌面兜底，也是历史行为） */
function downloadViaAnchor(input: ShareFileInput): ShareOutcome {
  const blob = new Blob([input.content], { type: input.mime })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = input.filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
  return 'downloaded'
}

/**
 * 把文本文件交给系统（分享或下载）。
 *
 * 返回 `cancelled` 时调用方**不要**报错——用户主动关掉了分享面板。
 * 返回 `failed` 才提示失败，并应建议换一种方式（例如在桌面端导出）。
 */
export async function shareTextFile(input: ShareFileInput): Promise<ShareOutcome> {
  if (IS_MOBILE_SHELL) {
    try {
      return await shareViaCapacitor(input)
    } catch (error) {
      // 原生路径失败（插件缺失 / 权限异常）仍可退回下载，不让用户彻底拿不到文件
      if (isCancelError(error)) return 'cancelled'
      console.warn('[export] 原生分享失败，回退到下载', error)
    }
  }

  try {
    const shared = await shareViaWebShare(input)
    if (shared) return shared
  } catch (error) {
    if (isCancelError(error)) return 'cancelled'
    console.warn('[export] Web Share 失败，回退到下载', error)
  }

  try {
    return downloadViaAnchor(input)
  } catch (error) {
    console.warn('[export] 下载失败', error)
    return 'failed'
  }
}
