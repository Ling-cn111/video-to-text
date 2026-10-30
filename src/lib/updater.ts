/**
 * 桌面端自动更新（Tauri updater）。
 *
 * ## 分层
 *
 * 这个文件只放**纯函数与类型**，不静态 import `@tauri-apps/*`。原因：
 * `e2e/` 里的 Node 测试要 import 本模块跑版本比较，而 Tauri 的 npm 包在
 * Node 环境下会直接抛（没有 `window.__TAURI_INTERNALS__`）。真正的插件调用
 * 放在 `use-app-update.ts` 里用动态 `import()` 加载，只在桌面壳内执行。
 *
 * ## 为什么不用「每次启动静默下载」
 *
 * 安装包 2.4 MB，国内到 GitHub 常要几十秒甚至超时。静默下载会让用户在毫无
 * 察觉的情况下占用带宽，失败也无从得知。这里的做法是：**检查自动、下载手动**。
 * 检查失败完全静默（不弹 toast），因为最常见的原因就是网络到不了 GitHub，
 * 弹出来只是噪音；用户主动点「检查更新」时才把错误显示出来。
 *
 * ## 端点在 GitHub 上，国内可能不可达
 *
 * `plugins.updater.endpoints` 指向 `releases/latest/download/latest.json`。
 * 这是 Tauri 官方推荐的零后端方案，但 `github.com` 在国内不稳定——而需要
 * 更新的恰恰是这些用户。当前取舍：
 *
 * - 检查失败 → 静默，界面不显示任何东西（不制造「更新坏了」的错觉）
 * - 手动检查失败 → 明确告知「连不上更新服务器」，并给出 Release 页链接
 * - 自动更新可用时，它就是最优路径（能连 GitHub 的用户直接一步到位）
 *
 * 后续若要覆盖国内用户，需要把 `latest.json` 与安装包都放到可达域名上
 * （例如阿里云 FC 反代），属于独立改造，不在本模块职责内。
 */

/** 更新检查的端点（与 tauri.conf.json 的 plugins.updater.endpoints 保持一致） */
export const UPDATE_ENDPOINT =
  'https://github.com/Ling-cn111/video-to-text/releases/latest/download/latest.json'

/** 手动下载页：检查失败时的兜底出口 */
export const RELEASES_PAGE = 'https://github.com/Ling-cn111/video-to-text/releases/latest'

/** 启动后多久做第一次检查（毫秒）。太早会与首屏渲染、转写请求抢带宽 */
export const FIRST_CHECK_DELAY_MS = 8000

/** 之后每隔多久复查一次（毫秒）。4 小时：桌面端常驻，不必更频繁 */
export const RECHECK_INTERVAL_MS = 4 * 60 * 60 * 1000

/** 检查请求超时（毫秒）。GitHub 在国内可能长时间不响应，超时后静默放弃 */
export const CHECK_TIMEOUT_MS = 15000

export interface UpdateInfo {
  /** 服务端公布的新版本号 */
  version: string
  /** 当前运行版本 */
  currentVersion: string
  /** 发布日期（RFC 3339），服务端没给就是 undefined */
  date?: string
  /** 发布说明（Markdown） */
  body?: string
}

export type UpdateCheckResult =
  | { kind: 'unsupported' }
  | { kind: 'up-to-date'; version: string }
  | { kind: 'available'; update: UpdateInfo }
  | { kind: 'error'; message: string }

/**
 * 解析版本号为数字数组。
 *
 * 只取开头的 `主.次.修订`，预发布后缀（`-beta.1`）与构建元数据（`+sha`）丢掉：
 * Tauri 的版本比较对预发布有严格语义，而本项目从不发预发布版，简化后行为
 * 可预测。无法解析时返回 `null`，调用方按「不比当前新」处理（宁可漏更新，
 * 不要因为解析失败而误报有更新）。
 */
export function parseVersion(version: string): [number, number, number] | null {
  const match = /^(\d+)\.(\d+)\.(\d+)/.exec(version.trim())
  if (!match) return null
  return [Number(match[1]), Number(match[2]), Number(match[3])]
}

/**
 * 比较两个版本号：`a > b` 返回正数，相等返回 0，`a < b` 返回负数。
 * 任一无法解析时返回 0（视为相同 → 不提示更新）。
 */
export function compareVersions(a: string, b: string): number {
  const left = parseVersion(a)
  const right = parseVersion(b)
  if (!left || !right) return 0
  for (let i = 0; i < 3; i += 1) {
    if (left[i] !== right[i]) return left[i] - right[i]
  }
  return 0
}

/** 服务端版本是否比当前版本新。解析失败一律 false。 */
export function isNewerVersion(remote: string, current: string): boolean {
  return compareVersions(remote, current) > 0
}

/** 字节数转可读文本，用于下载进度 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  // 字节不带小数，其余保留一位
  return unit === 0 ? `${Math.round(value)} B` : `${value.toFixed(1)} ${units[unit]}`
}

/**
 * 把下载进度折算成百分比。
 *
 * `total` 未知时返回 `null`（进度条切成不确定态），不要瞎猜——猜出来的
 * 百分比会来回跳，比不显示更让人困惑。
 */
export function progressPercent(downloaded: number, total?: number): number | null {
  if (!total || total <= 0) return null
  const ratio = (downloaded / total) * 100
  return Math.max(0, Math.min(100, Math.round(ratio)))
}

/**
 * 截断发布说明，避免把整篇 Markdown 塞进对话框。
 *
 * 按字符数截断而不是按行：发布说明里表格和代码块都可能很长，按行截断
 * 容易在表格中间断掉，反而更难读。
 */
export function truncateNotes(body: string | undefined, limit = 600): string {
  if (!body) return ''
  const text = body.trim()
  if (text.length <= limit) return text
  return `${text.slice(0, limit).trimEnd()}…`
}

/**
 * 判断是否该在这个环境启用自动更新。
 *
 * 三个条件缺一不可：
 * - 桌面壳（`IS_DESKTOP_SHELL`）：网页端刷新即最新，Android 走 APK 覆盖安装
 * - 生产构建：`tauri dev` 下的可执行文件没有签名，检查到更新也装不上
 * - 非测试环境：Playwright 跑的静态站点里没有 Tauri 桥
 */
export function shouldEnableUpdater(options: {
  isDesktopShell: boolean
  isProduction: boolean
}): boolean {
  return options.isDesktopShell && options.isProduction
}
