/**
 * 桌面端构建入口（Tauri 静态导出）。
 *
 * 实际逻辑已抽到 `scripts/build-shell.mjs`（桌面端与移动端共用同一套静态导出）。
 * 这个文件保留为薄包装，因为 `src-tauri/tauri.conf.json` 的 beforeBuildCommand
 * 与 `package.json` 的 build:desktop 都指向它。
 *
 * 用法：node scripts/build-desktop.mjs
 * 后端地址取值顺序：SHELL_API_BASE > DESKTOP_API_BASE（旧名）> .env.local 的 BACKEND_ORIGIN
 */
import { spawnSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const result = spawnSync(process.execPath, [join(ROOT, 'scripts', 'build-shell.mjs'), 'desktop'], {
  cwd: ROOT,
  stdio: 'inherit',
})
process.exit(result.status ?? 1)
