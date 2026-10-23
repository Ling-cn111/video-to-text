/**
 * 最小 Service Worker：应用外壳可安装、可离线打开。
 *
 * - 导航请求：网络优先，失败回退缓存的首页外壳（SPA 客户端路由接管）
 * - 静态资源（/_next/static、/icons、/covers）：缓存优先，后台补种
 * - /api/*：永不缓存（Mock 进度接口对时间敏感）
 *
 * 版本变更需同步提升 CACHE_NAME：activate 时会删除所有非当前版本的缓存，
 * 老客户端因此能拿到新策略（图标预缓存是 v2 新增）。
 */
const CACHE_NAME = 'vtt-shell-v2'
const PRECACHE_URLS = [
  '/',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/apple-touch-icon.png',
]

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      // 只清理本应用的历史外壳缓存，同源其它缓存（若有）不动
      .then((keys) =>
        Promise.all(keys.filter((key) => key.startsWith('vtt-shell-') && key !== CACHE_NAME).map((key) => caches.delete(key))),
      )
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return
  if (url.pathname.startsWith('/api/')) return

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone()
          caches.open(CACHE_NAME).then((cache) => cache.put('/', copy)).catch(() => {})
          return response
        })
        .catch(() => caches.match('/').then((cached) => cached ?? Response.error())),
    )
    return
  }

  const isStaticAsset =
    url.pathname.startsWith('/_next/static/') ||
    url.pathname.startsWith('/icons/') ||
    url.pathname.startsWith('/covers/')

  if (isStaticAsset) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached
        return fetch(request).then((response) => {
          const copy = response.clone()
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy)).catch(() => {})
          return response
        })
      }),
    )
  }
})
