import type { MetadataRoute } from 'next'

import { SITE_URL } from '@/lib/site'

/**
 * robots.txt：此前完全没有，搜索引擎无法知道规范地址。
 *
 * 只放行公开页面。任务页（/processing、/result）依赖 ?id= 参数，
 * 抓取下来只是一张空壳，且任务 7 天后过期，收录了也是死链，故 Disallow。
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: '*',
        allow: '/',
        disallow: ['/processing', '/result', '/backend-api/'],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  }
}
