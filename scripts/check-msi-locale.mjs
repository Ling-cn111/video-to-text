/**
 * MSI 中文乱码修复说明 + 校验脚本。
 *
 * 背景：Tauri 默认 WiX locale 是 en-us / codepage 1252，productName 含中文时
 * light.exe 报 LGHT0311。修复方式见 src-tauri/wix/zh-CN.wxl（改为 2052 / 936）。
 *
 * 本脚本做两件事：
 * 1. 打印当前 tauri.conf.json 里的 wix 语言配置
 * 2. 若已构建过 MSI，读取其 ProductName 属性，确认中文没被破坏
 *
 * 用法：node scripts/check-msi-locale.mjs
 */
import { execFileSync } from 'node:child_process'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const CONFIG = join(ROOT, 'src-tauri', 'tauri.conf.json')
const MSI_DIR = join(ROOT, 'src-tauri', 'target', 'release', 'bundle', 'msi')

const config = JSON.parse(readFileSync(CONFIG, 'utf8'))
const wix = config.bundle?.windows?.wix
console.log('productName :', config.productName)
console.log('wix.language:', JSON.stringify(wix?.language ?? '(未配置，将回退 en-US/1252)'))

if (!wix?.language) {
  console.warn('[warn] 未配置 wix 语言：中文 productName 会导致 LGHT0311 构建失败')
  process.exitCode = 1
}

if (!existsSync(MSI_DIR)) {
  console.log('[info] 尚未构建 MSI，跳过产物校验')
  process.exit(process.exitCode ?? 0)
}

const msi = readdirSync(MSI_DIR).find((name) => name.endsWith('.msi'))
if (!msi) {
  console.log('[info] msi 目录里没有 .msi 文件')
  process.exit(process.exitCode ?? 0)
}

// 用 Windows Installer COM 读 ProductName，能真实验证数据库里的中文字符串。
// 注意：PowerShell 默认按控制台代码页输出，中文会变问号，末尾强制转 UTF-8 输出。
const script = `
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$installer = New-Object -ComObject WindowsInstaller.Installer
$db = $installer.GetType().InvokeMember('OpenDatabase','InvokeMethod',$null,$installer,@('${join(MSI_DIR, msi).replace(/'/g, "''")}',0))
$view = $db.GetType().InvokeMember('OpenView','InvokeMethod',$null,$db,@("SELECT Value FROM Property WHERE Property='ProductName'"))
$view.GetType().InvokeMember('Execute','InvokeMethod',$null,$view,$null)
$rec = $view.GetType().InvokeMember('Fetch','InvokeMethod',$null,$view,$null)
$rec.GetType().InvokeMember('StringData','GetProperty',$null,$rec,1)
`
try {
  // -OutputFormat Text + UTF-8 解码，避免中文经控制台代码页损坏
  const raw = execFileSync('powershell.exe', ['-NoProfile', '-Command', script], { encoding: 'buffer' })
  const out = raw.toString('utf8').trim()
  console.log('msi file    :', msi)
  console.log('ProductName :', out)
  if (out !== config.productName) {
    console.warn(`[warn] MSI 内 ProductName（${out}）与配置（${config.productName}）不一致`)
    process.exitCode = 1
  } else {
    console.log('[ok] MSI 内中文产品名正确')
  }
} catch (error) {
  console.warn('[warn] 读取 MSI 属性失败：', error.message)
  process.exitCode = 1
}
