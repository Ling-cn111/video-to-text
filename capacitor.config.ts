import type { CapacitorConfig } from '@capacitor/cli'

/**
 * Capacitor Android 配置。
 *
 * 关键点：
 * - `webDir: 'out'`：Next 静态导出目录（不是 Capacitor 默认的 `www` / `public`），
 *   由 `pnpm build:mobile` 生成，`npx cap sync android` 复制进
 *   `android/app/src/main/assets/public/`。
 * - `server.androidScheme: 'https'` 是默认值，这里显式写出来是为了锁住它：
 *   WebView 里 `https://localhost` 属于 secure context，`navigator.clipboard.writeText`
 *   才可用（结果页点时间戳复制靠它）。改成 `http` 会让 Clipboard API 静默降级到
 *   `execCommand`，在部分 WebView 上失效。
 * - **不配 `server.url`**：配了就是「壳里加载远程网页」，前端资源又回到被墙的
 *   vercel.app，等于白做。资源必须打进 APK。
 */
const config: CapacitorConfig = {
  appId: 'com.videototext.app',
  appName: '视频转文字',
  webDir: 'out',
  server: {
    // 保持 https：见上面 secure context 说明，不要改成 http
    androidScheme: 'https',
  },
}

export default config
