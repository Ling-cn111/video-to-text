/**
 * 后端请求地址解析（两种运行形态共用一套调用代码）。
 *
 * - web（Node 服务 / Vercel）：NEXT_PUBLIC_API_BASE 为空，请求同源相对路径
 *   `/backend-api/*`，由 Next rewrites（见 next.config.mjs）在服务端代理到真实后端，
 *   浏览器不跨域，后端无需为浏览器单独配 CORS。
 * - 桌面端（Tauri 静态导出）：没有 Node 服务端，rewrites 不存在，
 *   构建时注入 NEXT_PUBLIC_API_BASE=<后端绝对地址>，直接跨域请求后端 /api/*。
 *   后端 CORS 已按 CORS_ORIGINS 放行（部署时设为 *），无需额外配置。
 */

/** 编译期常量：Next 只内联字面量形式的 process.env.NEXT_PUBLIC_*，故不能动态取值 */
const RAW_BASE = process.env.NEXT_PUBLIC_API_BASE ?? ''

/** 去掉末尾斜杠，避免拼出 //api/xxx */
export const API_BASE = RAW_BASE.replace(/\/+$/, '')

/**
 * 后端接口地址。`path` 传后端路由（如 '/parse'，对应后端 POST /api/parse）。
 * web → /backend-api/parse；桌面端 → https://<后端>/api/parse
 */
export function apiUrl(path: string): string {
  return API_BASE ? `${API_BASE}/api${path}` : `/backend-api${path}`
}
