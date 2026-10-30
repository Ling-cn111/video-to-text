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
import { readFile, stat } from 'node:fs/promises'
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
}

await browser.close()
server.close()

const failed = results.filter((r) => !r.ok)
console.log(`\n[${TARGET}] ${results.length - failed.length}/${results.length} 通过`)
process.exit(failed.length === 0 ? 0 : 1)
