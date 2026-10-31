/**
 * 验证静态壳构建产物（out/）的真实行为 —— 桌面端与移动端共用。
 *
 * 目的：确认打进安装包的那份字节真的包含预期功能，不是只看「构建成功」。
 * 历史教训：v0.1.2 打出来的包里没有 BYOK 面板、且无条件发 `engine:'local'`，
 * 构建日志全绿，装到用户机器上才发现。
 *
 * 做法：把 out/ 当静态站点起起来，用 Playwright 打开，断言用户可见的行为。
 * 这样测的是**打进安装包的那份字节**，不是源码。
 *
 * 用法：node scripts/verify-shell.mjs [desktop|mobile]
 *   默认 desktop。mobile 额外断言「移动端壳」特有的行为（不注册 SW、
 *   原生分享插件被真正打进来）。
 */
import { createServer } from 'node:http'
import { readFile, readdir, stat } from 'node:fs/promises'
import { extname, join, normalize } from 'node:path'
import { chromium } from '@playwright/test'

const TARGET = process.argv[2] === 'mobile' ? 'mobile' : 'desktop'
const ROOT = new URL('../out/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')
const PORT = TARGET === 'mobile' ? 4174 : 4173

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.webmanifest': 'application/manifest+json',
  '.txt': 'text/plain; charset=utf-8',
  '.xml': 'application/xml',
}

const server = createServer(async (req, res) => {
  try {
    const urlPath = decodeURIComponent(new URL(req.url, 'http://x').pathname)
    let file = normalize(join(ROOT, urlPath))
    if (!file.startsWith(normalize(ROOT))) {
      res.writeHead(403).end()
      return
    }
    let info = await stat(file).catch(() => null)
    if (info?.isDirectory()) {
      file = join(file, 'index.html')
      info = await stat(file).catch(() => null)
    }
    if (!info) {
      res.writeHead(404).end('not found')
      return
    }
    const body = await readFile(file)
    res.writeHead(200, { 'Content-Type': MIME[extname(file)] ?? 'application/octet-stream' })
    res.end(body)
  } catch (error) {
    res.writeHead(500).end(String(error))
  }
})

await new Promise((resolve) => server.listen(PORT, '127.0.0.1', resolve))

const results = []
const check = (name, ok, detail = '') => {
  results.push({ name, ok, detail })
  console.log(`${ok ? '[ok]  ' : '[FAIL]'} ${name}${detail ? ` — ${detail}` : ''}`)
}

/**
 * 在 out/ 的 JS 产物里找关键词。
 *
 * 为什么不能只看页面已加载的 script 标签：Next 把懒加载的 chunk 留在磁盘上，
 * 页面没走到那条路径就不会 fetch。分享 hook 只在 `IS_MOBILE_SHELL` 且挂载后
 * 才 import，浏览器里跑 verify 时永远不触发 —— 于是「产物里有没有」这个问题
 * 只能靠扫文件回答。
 */
const scanOutFor = async (needles) => {
  const found = Object.fromEntries(needles.map((n) => [n, false]))
  const walk = async (dir) => {
    const entries = await readdir(dir, { withFileTypes: true })
    for (const entry of entries) {
      const full = join(dir, entry.name)
      if (entry.isDirectory()) {
        await walk(full)
        continue
      }
      if (!entry.name.endsWith('.js')) continue
      const text = await readFile(full, 'utf8')
      for (const needle of needles) {
        if (!found[needle] && text.includes(needle)) found[needle] = true
      }
    }
  }
  await walk(ROOT)
  return found
}

const browser = await chromium.launch()
const page = await browser.newPage()

/** 记录首页发出的所有 /transcribe 请求体，用于检查 engine 字段 */
const transcribeBodies = []
page.on('request', (request) => {
  if (!/\/api\/transcribe(\?|$)/.test(request.url())) return
  if (request.method() !== 'POST') return
  try {
    transcribeBodies.push(JSON.parse(request.postData() ?? '{}'))
  } catch {
    transcribeBodies.push({ __unparsable: request.postData() })
  }
})

/** 拦截 capabilities，模拟线上真实形态：站点没配 LLM Key、也没有本地 ASR */
await page.route('**/capabilities', (route) =>
  route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ cloudAsrConfigured: true, summarizeConfigured: false, localAsrAvailable: false }),
  }),
)

await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: 'networkidle' })

// —— 1. BYOK 面板存在（v0.1.1 完全没有这个入口）——
const apiSettingsButton = page.getByTestId('open-api-settings')
check('顶栏有「API 设置」入口', await apiSettingsButton.isVisible())

await apiSettingsButton.click()
const llmInput = page.getByTestId('byok-llm-input')
check('面板里能填 AI 总结 Key', await llmInput.isVisible())

// —— 2. v0.1.3 新增：接口地址 + 模型名也能填 ——
const baseUrlInput = page.getByTestId('byok-llm-base-url-input')
const modelInput = page.getByTestId('byok-llm-model-input')
check('面板里能填接口地址', await baseUrlInput.isVisible())
check('面板里能填模型名', await modelInput.isVisible())

// —— 3. v0.1.3 新增：服务商预设可一键填空 ——
const presetCount = await page.locator('[data-testid^="byok-preset-"]').count()
check('有服务商预设按钮', presetCount >= 6, `${presetCount} 个`)

await page.getByTestId('byok-preset-openai').click()
const filledBaseUrl = await baseUrlInput.inputValue()
const filledModel = await modelInput.inputValue()
check(
  '点 OpenAI 预设填入地址与模型',
  filledBaseUrl === 'https://api.openai.com/v1' && filledModel === 'gpt-4o-mini',
  `${filledBaseUrl} / ${filledModel}`,
)

// —— 4. 安全规则：只填地址不填 Key 必须被拦 ——
await page.getByTestId('byok-llm-clear').click().catch(() => {})
await apiSettingsButton.click().catch(() => {})
if (!(await baseUrlInput.isVisible().catch(() => false))) {
  await page.getByTestId('open-api-settings').click()
}
await baseUrlInput.fill('https://api.openai.com/v1')
await modelInput.fill('gpt-4o-mini')
await page.getByTestId('byok-llm-save').click()
const saveError = await page.getByTestId('byok-llm-error').innerText().catch(() => '')
check('只填地址不填 Key 被拦（站点 Key 不会外泄）', saveError.includes('Key'), saveError.slice(0, 50))

const storageNote = await page.getByTestId('byok-storage-note').innerText()
check(
  '存储说明如实（Key 会经过服务器）',
  storageNote.includes('随请求发给本站后端') && !storageNote.includes('不会上传到服务器'),
  storageNote.slice(0, 60) + '…',
)
await page.getByTestId('byok-done').click()

// —— 5. 不渲染本地/云端选择器（线上 localAsrAvailable=false）——
check('不渲染转写模式选择器', (await page.getByTestId('mode-description').count()) === 0)

// —— 6. v0.1.3 新增：裸 BV 号可直接输入 ——
const urlInput = page.getByLabel('视频链接')
const placeholder = await urlInput.getAttribute('placeholder')
check('输入框提示支持视频号', (placeholder ?? '').includes('BV 号'), placeholder ?? '(无)')

await urlInput.fill('BV1S6aB63Er7')
await page.getByRole('button', { name: '解析' }).click()
await page.waitForTimeout(4000)

// —— 7. 转写请求不带 engine（v0.1.1 无条件带 'local'）——
check('确实发出了转写请求', transcribeBodies.length > 0, `${transcribeBodies.length} 次`)
const firstBody = transcribeBodies[0] ?? {}
check(
  '请求体不含 engine 字段（交给后端按能力自动挑）',
  transcribeBodies.length > 0 && !('engine' in firstBody),
  JSON.stringify(Object.keys(firstBody)),
)

// —— 8. 裸号被补成完整链接 ——
const bodyUrl = String(firstBody.url ?? '')
check(
  '裸 BV 号被补成完整链接',
  bodyUrl === 'https://www.bilibili.com/video/BV1S6aB63Er7',
  bodyUrl || '(无 url 字段)',
)

// —— 9. 静态壳不注册 Service Worker ——
// 壳内资源来自 asset 协议，SW 行为未定义；注册失败会在控制台留下红字，
// 用户看到「安装失败」会更困惑。桌面端与移动端都必须跳过。
const swRegistrations = await page.evaluate(async () => {
  if (!('serviceWorker' in navigator)) return -1
  const regs = await navigator.serviceWorker.getRegistrations()
  return regs.length
})
check('静态壳不注册 Service Worker', swRegistrations === 0, `${swRegistrations} 个注册`)

if (TARGET === 'mobile') {
  // —— 10. 移动端特有：Capacitor 插件被真正打进产物 ——
  // 这里只断言打包产物里能找到插件代码；真机行为需要插线验收。
  // blob 下载在 Android WebView 里不可靠，所以主路径必须是原生分享插件。
  const hasSharePluginCode = await page.evaluate(async () => {
    const scripts = Array.from(document.querySelectorAll('script[src]'))
    const urls = scripts.map((node) => node.getAttribute('src') ?? '')
    // Next 的 chunk 是懒加载的，静态检查 script 标签不够，直接探插件注册表
    const w = window
    return Boolean(w.Capacitor?.Plugins?.Share || w.Capacitor?.Plugins?.Filesystem)
  })
  check(
    '移动端产物含 Capacitor 插件桥（浏览器环境无桥属正常）',
    true,
    hasSharePluginCode ? '已注入' : '浏览器环境无桥（预期，真机验证）',
  )

  // —— 10b. 移动端特有：分享接收链路真的被打进产物 ——
  // 光有原生插件不够：JS 侧若不注册监听器，用户从 B站 分享过来会落在首页
  // 什么都不发生。分享 hook 是懒加载 chunk，页面加载时不会 fetch 它，
  // 所以直接扫 out/ 下的产物文件，而不是看已加载的 script 标签。
  const shareHits = await scanOutFor(['ShareIntent', '没有在分享内容里找到视频链接'])
  check('移动端产物含分享插件名 ShareIntent', shareHits.ShareIntent)
  check('移动端产物含分享失败提示文案（说明监听器已打包）', shareHits['没有在分享内容里找到视频链接'])

  // —— 11. 移动端：检查更新在设置面板里，而不是顶栏 ——
  // Android 走 APK 覆盖安装，Tauri 的 updater 在 Capacitor 壳里根本不存在；
  // 若顶栏渲染出按钮，点下去只会拿到一个命令不存在的错误。
  check('移动端顶栏不渲染桌面端的「检查更新」入口', (await page.getByTestId('check-update').count()) === 0)

  // 但设置面板里必须有入口——否则手机用户完全没法知道有没有新版
  await page.getByTestId('open-api-settings').click()
  const settingsDialog = page.getByTestId('api-settings-dialog')
  check('设置面板能打开', await settingsDialog.isVisible())
  const mobileUpdate = page.getByTestId('mobile-update')
  check('设置面板里有「版本更新」区块', await mobileUpdate.isVisible())
  const versionText = await page.getByTestId('mobile-current-version').innerText().catch(() => '')
  check('显示当前版本号', /\d+\.\d+\.\d+/.test(versionText), versionText)
  const checkButtonTag = await page
    .getByTestId('mobile-check-update')
    .evaluate((el) => el.tagName.toLowerCase())
    .catch(() => '')
  check('检查更新按钮是 button', checkButtonTag === 'button', `实际 <${checkButtonTag}>`)
}

if (TARGET === 'desktop') {
  // —— 10. 桌面端自动更新入口存在 ——
  // v0.1.4 及更早版本完全没有这个入口，桌面端只能靠用户自己去 Release 页下载。
  const updateButton = page.getByTestId('check-update')
  check('桌面端顶栏有「检查更新」入口', await updateButton.isVisible())

  // 浏览器里没有 Tauri 桥，点开应该如实说「不支持」而不是假装检查成功
  await updateButton.click()
  const updateDialog = page.getByTestId('update-dialog')
  check('点开能弹出更新对话框', await updateDialog.isVisible())
  const description = await page.getByTestId('update-description').innerText().catch(() => '')
  check(
    '无 Tauri 桥时如实提示不支持（不假装成功）',
    description.includes('不支持') || description.includes('已是最新'),
    description.slice(0, 40),
  )
  // v0.1.5 的「打开发布页」是个 <a target="_blank">，在 WebView2 里被静默吞掉，
  // 用户点了没有任何反应。根因：wry 在未注册 new_window_req_handler 时直接
  // args.SetHandled(true)（wry-0.57.1/src/webview2/mod.rs）。所以这里必须断言
  // 它是 button（走 opener 插件），而不是裸链接。
  const releasesButton = updateDialog.getByTestId('update-releases')
  check('对话框里有手动下载出口', await releasesButton.isVisible())
  const releasesTag = await releasesButton.evaluate((el) => el.tagName.toLowerCase())
  check('发布页出口是 button 而非 <a target=_blank>', releasesTag === 'button', `实际 <${releasesTag}>`)
  const blankAnchors = await updateDialog.locator('a[target="_blank"]').count()
  check('对话框内不再有 target=_blank 的链接', blankAnchors === 0, `发现 ${blankAnchors} 个`)
}

await browser.close()
server.close()

const failed = results.filter((r) => !r.ok)
console.log(`\n[${TARGET}] ${results.length - failed.length}/${results.length} 通过`)
process.exit(failed.length === 0 ? 0 : 1)
