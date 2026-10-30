/**
 * Android 返回键的行为决策（纯函数，便于单测）。
 *
 * 为什么单独抽出来：真机上的返回键依赖原生插件，浏览器里跑不出来；
 * 但「按下返回该做什么」这一步是纯逻辑，抽出来就能用 Node 断言，
 * 不必等插线才能验证。
 *
 * ## 为什么不用 `history.state.idx`
 *
 * 这个 hook 的第一版用 `history.state.idx > 0` 判断「应用内还有没有上一页」。
 * 那是 **Pages Router** 的行为：App Router 的 `pushState` 只写
 * `{ __NA, __PRIVATE_NEXTJS_INTERNALS_TREE }`，**没有 `idx`**（见
 * `node_modules/next/dist/client/components/app-router.js`）。
 * 于是 `idx` 恒为 `undefined` → 回落成 0 → 永远认为「已在第一页」→
 * `router.back()` 一次都没被调用过。真机表现就是「结果页按返回没反应」。
 *
 * ## 为什么按 pathname 判，而不是只看 `canGoBack`
 *
 * 只看 `canGoBack` 会出现「在首页按返回，退回上一个结果页」这种反直觉行为：
 * 结果页的「返回首页」按钮走的是 `router.push('/')`，会**新增**一条历史，
 * 历史栈变成 [首页, 结果页, 首页]，此时 `canGoBack` 为真。
 * 而首页在本应用里就是根：在根上按返回 = 想退出应用。
 */

/** 应用根路径。本应用只有 `/`、`/processing`、`/result` 三个页面。 */
export const HOME_PATH = '/'

/** 返回键动作：回退一页 / 直接回首页 / 退出应用。 */
export type BackAction = 'back' | 'home' | 'exit'

/**
 * `pathname` 是否等价于首页。
 *
 * 静态壳开了 `trailingSlash: true`，导出的路径可能是 `/` 也可能是空串，
 * 两种都当首页处理，避免漏判后按返回「什么都不做」。
 */
export function isHomePath(pathname: string | null | undefined): boolean {
  if (pathname === null || pathname === undefined) return false
  return pathname === '' || pathname === HOME_PATH
}

/**
 * 决定返回键动作。
 *
 * @param pathname 当前路径（`usePathname()` 的返回值）
 * @param canGoBack Capacitor 事件里给的 `canGoBack`（WebView 历史里还有上一页）
 *
 * - 不在首页 + 有上一页 → `back`：正常回退，符合 Android 直觉
 * - 不在首页 + 没有上一页 → `home`：冷启动直达 `/result` 的情况，
 *   此时 back 会离开应用，直接回首页比退出应用更符合预期
 * - 已在首页 → `exit`：由调用方做「再按一次退出」二次确认
 */
export function resolveBackAction(
  pathname: string | null | undefined,
  canGoBack: boolean,
): BackAction {
  if (isHomePath(pathname)) return 'exit'
  return canGoBack ? 'back' : 'home'
}
