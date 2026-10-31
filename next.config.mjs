/**
 * 构建目标区分：
 * - 默认（Web：Vercel / 本地 pnpm build）：Node 服务模式，靠 rewrites 同源代理后端
 * - BUILD_TARGET=desktop（Tauri 桌面端）：静态导出 out/，无 Node 服务，直接请求后端绝对地址
 * - BUILD_TARGET=mobile（Capacitor Android）：与 desktop 同为静态导出，产物由 cap sync
 *   复制进 android/app/src/main/assets/public/
 *
 * 静态壳（desktop / mobile）与 Web 不能共存：静态导出不支持 rewrites / redirects / headers，
 * 壳内也不存在服务端代理。
 */
const BUILD_TARGET = process.env.BUILD_TARGET ?? "";
const isStaticShell = BUILD_TARGET === "desktop" || BUILD_TARGET === "mobile";

// 前端要知道自己跑的是哪个版本：移动端「检查更新」得拿它和 GitHub 上的最新版比。
// 桌面端从 Tauri 的 getVersion() 拿（安装包内嵌的版本），这里只服务静态壳的前端。
import { readFileSync } from "node:fs";
const PKG_VERSION = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")).version;

/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    NEXT_PUBLIC_APP_VERSION: PKG_VERSION,
  },
  ...(isStaticShell
    ? {
        // 产物写到 out/：桌面端由 Tauri 的 asset 协议加载（src-tauri/tauri.conf.json 的
        // frontendDist），移动端由 Capacitor 复制进 Android 工程。
        output: "export",
        // 导出为 /result/index.html 形式：壳按目录找 index.html，比 xxx.html 更稳
        trailingSlash: true,
        // 静态导出没有服务端 canary（dev 下 /_next/image 的 image optimizer），必须关闭
        images: { unoptimized: true },
      }
    : {
        async rewrites() {
          // 真实后端代理：客户端只发同源 /backend-api/* 请求，由 Next 服务端转发到
          // BACKEND_ORIGIN（服务端环境变量，默认本地 8000）。后端地址不暴露给浏览器。
          return [
            {
              source: "/backend-api/:path*",
              destination: `${process.env.BACKEND_ORIGIN ?? "http://localhost:8000"}/api/:path*`,
            },
          ];
        },
        async redirects() {
          // 旧链接兼容：/result/<taskId> → /result?id=<taskId>（桌面端静态导出要求
          // 固定路由，故内部导航改用查询参数，见 src/lib/routes.ts）。
          // 只影响 web；静态导出不支持 redirects，桌面端入口永远是查询参数形态。
          return [
            { source: "/processing/:taskId", destination: "/processing?id=:taskId", permanent: true },
            { source: "/result/:taskId", destination: "/result?id=:taskId", permanent: true },
          ];
        },
        async headers() {
          // 安全响应头。只加在 web 分支：静态导出（桌面端）由 Tauri asset 协议
          // 加载本地文件，没有 HTTP 响应头可加，写了也不生效。
          //
          // CSP 说明：
          // - script-src 需要 'unsafe-inline'：next-themes 在 <head> 内联一段脚本，
          //   用于在首帧前把 .dark 类写进 <html>（否则深色用户会看到白闪）。
          //   非 random 的 nonce 方案在静态预渲染下拿不到，暂不引入。
          // - img-src 允许 https:：视频封面来自 B站 CDN，域名不固定。
          // - connect-src 允许 https:：客户端只发同源 /backend-api/*，但桌面端
          //   同构代码在 web 构建里不会执行跨域请求，收紧到 'self' 会误伤预览环境。
          const csp = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob: https:",
            "font-src 'self' data:",
            "connect-src 'self' https:",
            "media-src 'self' https:",
            "worker-src 'self'",
            "manifest-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
            // 站点无任何外部来源，升级不安全请求即可
            "upgrade-insecure-requests",
          ].join("; ");

          return [
            {
              source: "/:path*",
              headers: [
                { key: "Content-Security-Policy", value: csp },
                // 防点击劫持（CSP frame-ancestors 已覆盖，这里给老浏览器兜底）
                { key: "X-Frame-Options", value: "DENY" },
                { key: "X-Content-Type-Options", value: "nosniff" },
                // 任务页 URL 含 taskId，跨站跳转时不泄露给第三方
                { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
                { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
                { key: "X-DNS-Prefetch-Control", value: "off" },
                {
                  key: "Strict-Transport-Security",
                  value: "max-age=63072000; includeSubDomains",
                },
              ],
            },
            {
              // 静态资源带内容哈希，可长期缓存
              source: "/_next/static/:path*",
              headers: [
                { key: "Cache-Control", value: "public, max-age=31536000, immutable" },
              ],
            },
            {
              // Service Worker 必须每次校验，否则老客户端长期跑旧外壳
              source: "/sw.js",
              headers: [{ key: "Cache-Control", value: "no-cache, no-store, must-revalidate" }],
            },
          ];
        },
      }),
};

export default nextConfig;
