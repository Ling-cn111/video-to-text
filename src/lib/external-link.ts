import { IS_DESKTOP_SHELL, IS_MOBILE_SHELL } from '@/lib/runtime'

/**
 * 在系统浏览器里打开外部链接。
 *
 * ## 为什么不能只写 `<a target="_blank">`
 *
 * 两个壳都不支持它，而且都不报错：
 *
 * - **桌面端（WebView2）**：Tauri 默认不注册 new-window 处理器，wry 在
 *   `new_window_req_handler` 为 None 时直接执行 `args.SetHandled(true)`
 *   （wry-0.57.1/src/webview2/mod.rs 的 `add_NewWindowRequested` 回调 else 分支），
 *   点击被静默吞掉。这就是 v0.1.5「打开发布页没反应」的根因。
 *   修法：走 opener 插件，由 Rust 侧调系统默认浏览器。
 *
 * - **移动端（Android WebView）**：Capacitor 的 `BridgeWebChromeClient` 没有覆写
 *   `onCreateWindow`，`window.open` 的新窗口请求无人接管 → 同样静默失败。
 *   但 `location.href` 会走 `BridgeWebViewClient.shouldOverrideUrlLoading` →
 *   `Bridge.launchIntent`（Bridge.java:414-425）：域名与应用不同源时
 *   `startActivity(Intent.ACTION_VIEW)` 交给系统浏览器，并返回 true 消费掉导航
 *   ——既打开了浏览器，也不会把 WebView 导航走。这是唯一验证过的路径。
 *
 * 网页端用 `window.open`，新标签页行为由浏览器决定。
 *
 * 返回 `false` 表示打不开（调用方应提示用户手动复制链接）。
 */
export async function openExternal(url: string): Promise<boolean> {
  if (typeof window === 'undefined') return false

  // 桌面壳：opener 插件（系统默认浏览器）
  if (IS_DESKTOP_SHELL && '__TAURI_INTERNALS__' in window) {
    try {
      const { openUrl } = await import('@tauri-apps/plugin-opener')
      await openUrl(url)
      return true
    } catch {
      // 插件缺失或权限被拒：不要静默，交给调用方兜底提示
      return false
    }
  }

  // 移动壳：location.href 走 Capacitor 的 launchIntent 分支，交给系统浏览器
  if (IS_MOBILE_SHELL) {
    try {
      window.location.href = url
      return true
    } catch {
      return false
    }
  }

  const opened = window.open(url, '_blank', 'noopener,noreferrer')
  // 弹窗被拦截时 opened 为 null，但链接本身有效——返回 false 让调用方提示
  return opened !== null
}
