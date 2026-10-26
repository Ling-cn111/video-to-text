import type { MetadataRoute } from 'next'

import { SITE_URL } from '@/lib/site'

/**
 * sitemap.xml：只列首页。
 *
 * 任务页靠 ?id= 定位，且任务本身 7 天后过期（TASK_TTL_SECONDS=604800），
 * 收录进 sitemap 只会给搜索引擎一堆死链，因此不列。
 */
export default function sitemap(): MetadataRoute.Sitemap {
  return [
    {
      url: `${SITE_URL}/`,
      lastModified: new Date(),
      changeFrequency: 'weekly',
      priority: 1,
    },
  ]
}
