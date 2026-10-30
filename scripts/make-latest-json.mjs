/**
 * 生成 Tauri 自动更新所需的 `latest.json`。
 *
 * ## 为什么需要这个文件
 *
 * Tauri 的 updater 插件不读 GitHub Release 的元数据，它只认一个静态 JSON：
 *
 * ```json
 * { "version": "0.1.5", "notes": "...", "pub_date": "...",
 *   "platforms": { "windows-x86_64": { "signature": "...", "url": "..." } } }
 * ```
 *
 * `signature` 是构建时用私钥对安装包做的签名（`tauri build` 在
 * `createUpdaterArtifacts: true` 时自动生成 `<安装包>.sig`），
 * `pubkey` 在 `tauri.conf.json` 里，两端配对才能安装 —— 这是防中间人替换
 * 安装包的关键，不是可选项。
 *
 * ## 为什么用脚本而不是手写
 *
 * 签名是几 KB 的 base64 单行文本，手抄一定会错行。而且版本号、文件名、
 * 日期三处必须与真实产物一致，手工同步迟早漂移。
 *
 * 用法：
 *   node scripts/make-latest-json.mjs --version 0.1.5 --notes-file notes.md --out latest.json
 *
 * 自动从 `src-tauri/target/release/bundle/` 找 `.sig` 与对应安装包。
 */
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const BUNDLE_DIR = join(ROOT, 'src-tauri', 'target', 'release', 'bundle')

/** GitHub Release 的下载前缀（资产名会被 gh 转成 ASCII，见 README 的说明） */
const DOWNLOAD_BASE = 'https://github.com/Ling-cn111/video-to-text/releases/download'

function parseArgs(argv) {
  const out = { version: '', notesFile: '', out: 'latest.json', tag: '' }
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i]
    if (key === '--version') out.version = argv[++i] ?? ''
    else if (key === '--notes-file') out.notesFile = argv[++i] ?? ''
    else if (key === '--out') out.out = argv[++i] ?? 'latest.json'
    else if (key === '--tag') out.tag = argv[++i] ?? ''
  }
  return out
}

const args = parseArgs(process.argv.slice(2))

if (!args.version) {
  console.error('[latest.json] 缺少 --version（例如 --version 0.1.5）')
  process.exit(1)
}
if (!/^\d+\.\d+\.\d+$/.test(args.version)) {
  console.error(`[latest.json] 版本号必须是 主.次.修订，当前为：${args.version}`)
  process.exit(1)
}

const tag = args.tag || `v${args.version}`

/** 在 bundle 目录下递归找文件名含指定关键字的文件 */
function findFile(dir, predicate) {
  if (!existsSync(dir)) return null
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      const nested = findFile(full, predicate)
      if (nested) return nested
    } else if (predicate(entry.name)) {
      return full
    }
  }
  return null
}

const version = args.version

// NSIS 安装包优先：Tauri updater 在 Windows 上默认用它做增量/静默安装，
// MSI 走的是另一条代码路径（msiexec），签名文件也不通用。
const nsisInstaller = findFile(BUNDLE_DIR, (name) => name.endsWith(`_${version}_x64-setup.exe`))
const nsisSig = nsisInstaller ? `${nsisInstaller}.sig` : null

if (!nsisInstaller) {
  console.error(`[latest.json] 没找到 NSIS 安装包：*_${version}_x64-setup.exe`)
  console.error(`[latest.json] 查找目录：${BUNDLE_DIR}`)
  console.error('[latest.json] 请先运行 pnpm tauri build（需设置 TAURI_SIGNING_PRIVATE_KEY）')
  process.exit(1)
}
if (!nsisSig || !existsSync(nsisSig)) {
  console.error(`[latest.json] 缺少签名文件：${nsisSig ?? '(安装包未找到)'}`)
  console.error('[latest.json] 签名由 createUpdaterArtifacts 生成，需要环境变量：')
  console.error('  TAURI_SIGNING_PRIVATE_KEY        私钥内容（或路径）')
  console.error('  TAURI_SIGNING_PRIVATE_KEY_PASSWORD  私钥口令')
  console.error('[latest.json] 没有签名就发版，等于让所有客户端无法自动更新。')
  process.exit(1)
}

const signature = readFileSync(nsisSig, 'utf8').trim()
if (signature.length < 100) {
  console.error(`[latest.json] 签名内容异常（${signature.length} 字节），疑似空文件`)
  process.exit(1)
}

// GitHub 会把中文文件名剥成 `_`（见 v0.1.1 的踩坑），所以资产名必须是 ASCII。
// 这里显式用 ASCII 名拼 URL，而不是拿本地文件名 —— 本地名是中文的。
const assetName = `video-to-text_${version}_x64-setup.exe`
const url = `${DOWNLOAD_BASE}/${tag}/${assetName}`

const notes = args.notesFile && existsSync(args.notesFile) ? readFileSync(args.notesFile, 'utf8').trim() : ''

const payload = {
  version,
  notes,
  // Tauri 要求 RFC 3339；用当前时间即可，它只用于展示
  pub_date: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'),
  platforms: {
    // Tauri 2 的 Windows 目标键是 windows-x86_64（64 位），不区分 NSIS/MSI
    'windows-x86_64': { signature, url },
  },
}

const outPath = join(ROOT, args.out)
writeFileSync(outPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8')

console.log(`[latest.json] 已生成：${outPath}`)
console.log(`[latest.json] 版本：${version}`)
console.log(`[latest.json] 安装包：${nsisInstaller}`)
console.log(`[latest.json] 下载地址：${url}`)
console.log(`[latest.json] 签名长度：${signature.length}`)
console.log('')
console.log('[latest.json] 上传前请确认：')
console.log(`  1. Release 的 tag 是 ${tag}`)
console.log(`  2. 资产名是 ${assetName}（ASCII，不能带中文）`)
console.log('  3. latest.json 与本文件一起上传到同一个 Release')
