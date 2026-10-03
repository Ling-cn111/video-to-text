/** @type {import('next').NextConfig} */
const nextConfig = {
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
};

export default nextConfig;
