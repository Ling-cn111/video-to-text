/**
 * 运行形态判定（构建期常量，便于 tree-shaking）。
 *
 * 三种形态共用同一份 `src/` 代码：
 * - web：Next Node 服务（Vercel / 本地），请求同源 `/backend-api/*`，由 rewrites 代理
 * - desktop：Tauri，静态导出 `out/` 打进安装包
 * - mobile：Capacitor Android，静态导出 `out/` 复制进 APK
 *
 * 后两者合称「静态壳」：没有 Node 服务端，资源来自本地安装目录，
 * 所以不注册 Service Worker（缓存只会拖慢更新，且在 asset 协议下行为未定义）。
 */

/**
 * 是否静态壳。
 *
 * `NEXT_PUBLIC_STATIC_SHELL` 是正式名字；`NEXT_PUBLIC_DESKTOP` 是桌面端 v0.1.3
 * 及更早版本的构建脚本仍在注入的旧名，保留兼容，避免老构建脚本静默失效。
 */
export const IS_STATIC_SHELL =
  process.env.NEXT_PUBLIC_STATIC_SHELL === 'true' || process.env.NEXT_PUBLIC_DESKTOP === 'true'

/** 是否移动端壳（Capacitor）。用于按需启用原生能力，web 与桌面端不加载。 */
export const IS_MOBILE_SHELL = process.env.NEXT_PUBLIC_MOBILE_SHELL === 'true'
