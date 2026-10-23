/**
 * 构建目标区分：
 * - 默认（Web：Vercel / 本地 pnpm build）：Node 服务模式，靠 rewrites 同源代理后端
 * - BUILD_TARGET=desktop（Tauri 桌面端）：静态导出 out/，无 Node 服务，直接请求后端绝对地址
 *
 * 两者不能共存：静态导出不支持 rewrites，桌面端也不存在服务端代理。
 */
const isDesktop = process.env.BUILD_TARGET === "desktop";

/** @type {import('next').NextConfig} */
const nextConfig = {
  ...(isDesktop
    ? {
        // 产物写到 out/，由 Tauri 的 asset 协议直接加载（src-tauri/tauri.conf.json 的 frontendDist）
        output: "export",
        // 导出为 /result/index.html 形式：Tauri 按目录找 index.html，比 xxx.html 更稳
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
      }),
};

export default nextConfig;
