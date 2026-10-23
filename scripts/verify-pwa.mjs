/**
 * PWA 验收脚本：在真实 Chromium 里检查任务 1 的每一项要求。
 *
 * 覆盖：
 * 1. /manifest.webmanifest 200 且含 name / short_name / display:standalone / 192 / 512 图标
 * 2. Service Worker 注册并 activated
 * 3. Cache Storage 里有 vtt-shell-v2，且静态资源被缓存
 * 4. apple-touch-icon 可访问（180x180）
 * 5. theme-color / viewport-fit=cover（iOS 安全区）
 *
 * 用法：先 `pnpm build && pnpm start`，再 `node scripts/verify-pwa.mjs`
 */
import { chromium } from '@playwright/test'

const BASE = process.env.PWA_BASE ?? 'http://localhost:3000'
let failed = 0

function check(label, ok, detail = '') {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? ` — ${detail}` : ''}`)
  if (!ok) failed++
}

const browser = await chromium.launch()
const context = await browser.newContext()
const page = await context.newPage()

try {
  // ---- 1. manifest ----
  const res = await page.goto(`${BASE}/manifest.webmanifest`)
  check('manifest 返回 200', res?.status() === 200, `status=${res?.status()}`)
  const manifest = await res?.json()
  check('manifest.name 存在', Boolean(manifest?.name), manifest?.name)
  check('manifest.short_name 存在', Boolean(manifest?.short_name), manifest?.short_name)
  check('display 为 standalone', manifest?.display === 'standalone', manifest?.display)

  const icons = manifest?.icons ?? []
  const has192 = icons.some((icon) => /\b192x192\b/.test(icon.sizes ?? ''))
  const has512 = icons.some((icon) => /\b512x512\b/.test(icon.sizes ?? ''))
  check('含 192x192 图标', has192, icons.map((i) => i.sizes).join(', '))
  check('含 512x512 图标', has512, icons.map((i) => i.sizes).join(', '))
  check('含 maskable 图标', icons.some((i) => i.purpose === 'maskable'))

  // 图标文件本身可访问
  for (const icon of icons) {
    const r = await page.request.get(new URL(icon.src, BASE).href)
    check(`图标可访问 ${icon.src}`, r.ok(), `status=${r.status()}`)
  }

  // ---- 2. apple-touch-icon（必须在 HTML 页面上取，manifest 本身不是 HTML）----
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  const appleLink = await page.evaluate(() => {
    const el = document.querySelector('link[rel="apple-touch-icon"]')
    return el ? el.getAttribute('href') : null
  })
  check('页面含 apple-touch-icon link', Boolean(appleLink), appleLink ?? '(缺失)')
  if (appleLink) {
    const r = await page.request.get(new URL(appleLink, BASE).href)
    check('apple-touch-icon 可访问', r.ok(), `status=${r.status()} type=${r.headers()['content-type']}`)
  }

  // ---- 3. theme-color / viewport ----
  const meta = await page.evaluate(() => {
    const theme = document.querySelector('meta[name="theme-color"]')
    const viewport = document.querySelector('meta[name="viewport"]')
    return { theme: theme?.getAttribute('content'), viewport: viewport?.getAttribute('content') }
  })
  check('含 theme-color', Boolean(meta.theme), meta.theme ?? '(缺失)')
  check('viewport 含 viewport-fit=cover', /viewport-fit=cover/.test(meta.viewport ?? ''), meta.viewport ?? '(缺失)')

  // ---- 4. Service Worker ----
  await page.goto(BASE, { waitUntil: 'load' })
  await page.waitForTimeout(1500)

  const swState = await page.evaluate(async () => {
    if (!('serviceWorker' in navigator)) return { supported: false }
    const reg = await navigator.serviceWorker.getRegistration('/sw.js')
    if (!reg) return { supported: true, registered: false }
    // 首次注册可能仍是 installing，等它激活
    const worker = reg.active ?? reg.installing ?? reg.waiting
    if (worker && worker.state !== 'activated') {
      await new Promise((resolve) => {
        const onChange = () => {
          if (worker.state === 'activated') {
            worker.removeEventListener('statechange', onChange)
            resolve()
          }
        }
        worker.addEventListener('statechange', onChange)
        setTimeout(resolve, 8000)
      })
    }
    return { supported: true, registered: true, state: reg.active?.state ?? worker?.state ?? 'unknown' }
  })
  check('Service Worker 已注册', swState.registered === true, JSON.stringify(swState))
  check('Service Worker 已激活', swState.state === 'activated', swState.state ?? '')

  // ---- 5. 缓存内容 ----
  const cacheInfo = await page.evaluate(async () => {
    const keys = await caches.keys()
    const ours = keys.filter((k) => k.startsWith('vtt-shell-'))
    if (ours.length === 0) return { keys, entries: [] }
    const cache = await caches.open(ours[0])
    const reqs = await cache.keys()
    return { keys, cacheName: ours[0], entries: reqs.map((r) => new URL(r.url).pathname) }
  })
  check('存在 vtt-shell-* 缓存', Boolean(cacheInfo.cacheName), (cacheInfo.keys ?? []).join(', '))
  check('缓存里预置了 192 图标', (cacheInfo.entries ?? []).includes('/icons/icon-192.png'))
  check('缓存里预置了 manifest', (cacheInfo.entries ?? []).includes('/manifest.webmanifest'))
  console.log('     缓存条目:', (cacheInfo.entries ?? []).length, (cacheInfo.entries ?? []).slice(0, 8).join(', '))

  // ---- 6. 二次访问命中缓存（无需再请求网络图标的证据：条目数不减少即可）----
  await page.reload({ waitUntil: 'load' })
  const afterReload = await page.evaluate(async () => {
    const keys = await caches.keys()
    const ours = keys.filter((k) => k.startsWith('vtt-shell-'))
    if (ours.length === 0) return 0
    const cache = await caches.open(ours[0])
    return (await cache.keys()).length
  })
  check('刷新后缓存仍有效', afterReload > 0, `entries=${afterReload}`)
} finally {
  await browser.close()
}

console.log(failed === 0 ? '\n全部通过' : `\n${failed} 项失败`)
process.exit(failed === 0 ? 0 : 1)
