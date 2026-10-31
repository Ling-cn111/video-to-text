import { IS_MOBILE_SHELL } from '@/lib/runtime'

/**
 * 应用内更新：下载 APK 并交给系统安装器（仅 Android 壳内可用）。
 *
 * ## 为什么下载走 Filesystem 的 downloadFile 而不是 fetch + blob
 *
 * GitHub Release 的 APK 直链会 302 到 `objects.githubusercontent.com`，
 * 那个域名**没有 CORS 头**，WebView 里 fetch 会直接失败。而
 * `@capacitor/filesystem` 的 `downloadFile` 是原生 HTTP 请求，不受同源策略
 * 约束，还能立刻落到磁盘——不需要把 4 MB 内容读进内存再写一遍。
 *
 * ## 为什么「安装」还要用户点两下
 *
 * Android 从设计上不允许应用静默安装自己（那是设备管理员 / 企业 MDM 的权限）。
 * 我们能收拢的只有「去浏览器 → 找下载文件 → 点开」这三步；最后「是否安装」
 * 必须由系统安装器弹窗问用户。外加 Android 8+ 还有一道「安装未知应用」的
 * 逐应用授权，用户第一次用会多一次跳转。
 *
 * 所以这里的流程是：`canInstall()` 查授权 → 没授权就 `requestPermission()`
 * 跳系统设置 → 回来再查 → 授权了才下载 + `install()`。
 */

/** APK 缓存的相对路径（相对 app cache 根目录）；必须与原生侧 `DEFAULT_PATH` 一致 */
export const APK_CACHE_PATH = 'updates/video-to-text-update.apk'

/** APK MIME 类型（系统安装器只认这一个） */
export const APK_MIME = 'application/vnd.android.package-archive'

/** 安装授权状态 */
export interface InstallPermissionState {
  /** 现在能不能直接拉起安装器 */
  allowed: boolean
  /** 需要用户先去系统设置里授权 */
  needsPermission: boolean
}

/** 应用内更新的阶段，UI 据此显示不同文案 */
export type InAppUpdateStage =
  | 'idle'
  | 'downloading'
  | 'need-permission'
  | 'installing'
  | 'failed'

/** ApkInstaller 原生插件的形状（只声明用到的方法） */
interface ApkInstallerPlugin {
  canInstall: () => Promise<InstallPermissionState>
  requestPermission: () => Promise<void>
  install: (options?: { path?: string }) => Promise<void>
}

/**
 * 拿到原生插件。非移动壳或插件没打进包时返回 null。
 *
 * 用 `registerPlugin` 而不是 `Capacitor.Plugins`——后者要求插件已被
 * Capacitor 的插件加载器登记，而本项目是 `registerPlugin(Class)` 手动注册的。
 */
async function getInstaller(): Promise<ApkInstallerPlugin | null> {
  if (!IS_MOBILE_SHELL) return null
  try {
    const { registerPlugin } = await import('@capacitor/core')
    return registerPlugin<ApkInstallerPlugin>('ApkInstaller')
  } catch (error) {
    console.warn('[update] 安装插件不可用', error)
    return null
  }
}

/** 查询是否已获得「安装未知应用」授权；非移动壳返回 null */
export async function checkInstallPermission(): Promise<InstallPermissionState | null> {
  const installer = await getInstaller()
  if (!installer) return null
  try {
    return await installer.canInstall()
  } catch (error) {
    console.warn('[update] 查询安装授权失败', error)
    return null
  }
}

/** 跳系统设置页授权「安装未知应用」；非移动壳返回 false */
export async function requestInstallPermission(): Promise<boolean> {
  const installer = await getInstaller()
  if (!installer) return false
  try {
    await installer.requestPermission()
    return true
  } catch (error) {
    console.warn('[update] 打不开安装授权设置页', error)
    return false
  }
}

/**
 * 下载 APK 到缓存目录。
 *
 * `progress` 回调收到 0–100 的整数百分比。`@capacitor/filesystem` 的
 * progress 事件默认关闭，要显式传 `progress: true` 且**必须**挂
 * `addListener('progress')` 才会真的分发（否则 iOS 会一直等一个没人消费的监听）。
 */
export async function downloadApk(
  url: string,
  onProgress?: (percent: number) => void,
): Promise<{ ok: true } | { ok: false; message: string }> {
  try {
    const { Filesystem, Directory } = await import('@capacitor/filesystem')

    const listener = onProgress
      ? await Filesystem.addListener(
          'progress',
          (event: { bytes: number; contentLength: number }) => {
            // contentLength 在服务器没给 Content-Length 时是 -1，此时只报「已下载多少」
            if (event.contentLength > 0) {
              onProgress(Math.min(100, Math.round((event.bytes / event.contentLength) * 100)))
            }
          },
        )
      : null

    try {
      await Filesystem.downloadFile({
        url,
        path: APK_CACHE_PATH,
        directory: Directory.Cache,
        // 必须显式打开，否则 progress 事件一次都不会来
        progress: onProgress ? true : undefined,
        recursive: true,
      })
    } finally {
      // 监听器不 remove 会一直挂在 Filesystem 上，多次检查更新会叠起来
      await listener?.remove()
    }
    return { ok: true }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    console.warn('[update] 下载 APK 失败', error)
    return { ok: false, message }
  }
}

/** 拉起系统安装器；返回失败原因便于提示 */
export async function installApk(): Promise<{ ok: true } | { ok: false; message: string }> {
  const installer = await getInstaller()
  if (!installer) return { ok: false, message: '当前环境不支持应用内安装' }
  try {
    await installer.install({ path: APK_CACHE_PATH })
    return { ok: true }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    console.warn('[update] 拉起安装器失败', error)
    return { ok: false, message }
  }
}

/**
 * 一次完整的应用内更新：查授权 → （必要时）授权 → 下载 → 安装。
 *
 * 返回 `need-permission` 时调用方应停下来等用户去设置里开权限，
 * 不要循环重试——`requestPermission` 只是把人送到设置页，回来才知道结果。
 */
export async function runInAppUpdate(
  apkUrl: string,
  onProgress?: (percent: number) => void,
): Promise<{ stage: InAppUpdateStage; message?: string }> {
  const permission = await checkInstallPermission()
  if (!permission) return { stage: 'failed', message: '当前环境不支持应用内安装' }
  if (permission.needsPermission) {
    const opened = await requestInstallPermission()
    return {
      stage: 'need-permission',
      message: opened
        ? '请在系统设置里允许「视频转文字」安装应用，然后回到这里再点一次'
        : '打不开系统设置页，请手动在设置里允许本应用安装应用',
    }
  }

  const downloaded = await downloadApk(apkUrl, onProgress)
  if (!downloaded.ok) {
    return { stage: 'failed', message: `下载失败：${downloaded.message}` }
  }

  const installed = await installApk()
  if (!installed.ok) {
    return { stage: 'failed', message: installed.message }
  }
  return { stage: 'installing' }
}
