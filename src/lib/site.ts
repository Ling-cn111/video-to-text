/**
 * 站点绝对地址：robots / sitemap / metadataBase 需要绝对 URL，不能靠请求头推断。
 *
 * 换自定义域名时只改这里（或用 NEXT_PUBLIC_SITE_URL 覆盖构建）。
 * 默认值仍是 Vercel 域名 —— 它国内不可达，但那是「可达性」问题，
 * 不影响它作为规范 URL 用于搜索引擎与 og 图。
 */
export const SITE_URL = (
  process.env.NEXT_PUBLIC_SITE_URL || 'https://video-to-text-six.vercel.app'
).replace(/\/+$/, '')
