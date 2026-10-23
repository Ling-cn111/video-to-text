/**
 * 桌面端构建脚本（Tauri 静态导出）。
 *
 * 与 web 构建的差异：
 * 1. BUILD_TARGET=desktop → next.config.mjs 切到 output:'export'（产物 out/）
 * 2. NEXT_PUBLIC_USE_MOCK=false → 走真实后端
 * 3. NEXT_PUBLIC_API_BASE=<后端绝对地址> → 无 Node 服务端，跨域直连 /api/*
 * 4. 临时移走 src/app/api：Mock Route Handlers 含 POST，静态导出不支持；
 *    桌面端用不到（USE_MOCK=false），构建后原样还原。
 *
 * 用法：node scripts/build-desktop.mjs
 * 后端地址取值顺序：DESKTOP_API_BASE > .env.local 的 BACKEND_ORIGIN
 */
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const API_DIR = join(ROOT, 'src', 'app', 'api')
const STASH_DIR = join(ROOT, '.desktop-build', 'api')

/** 从 .env.local 读单个键（不引依赖，格式简单够用） */
function readEnvLocal(key) {
  const file = join(ROOT, '.env.local')
  if (!existsSync(file)) return ''
  for (const rawLine of readFileSync(file, 'utf8').split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    const eq = line.indexOf('=')
    if (eq < 0) continue
    if (line.slice(0, eq).trim() !== key) continue
    return line.slice(eq + 1).trim().replace(/^['"]|['"]$/g, '')
  }
  return ''
}

const apiBase = (process.env.DESKTOP_API_BASE || readEnvLocal('BACKEND_ORIGIN')).replace(/\/+$/, '')

if (!apiBase) {
  console.error('[desktop] 缺少后端地址：请设置 DESKTOP_API_BASE，或在 .env.local 里配 BACKEND_ORIGIN')
  process.exit(1)
}
if (!/^https?:\/\//i.test(apiBase)) {
  console.error(`[desktop] 后端地址必须是绝对 http(s) URL，当前为：${apiBase}`)
  process.exit(1)
}

console.log(`[desktop] 后端地址：${apiBase}`)

/** 把 Mock Route Handlers 移出 app 目录，静态导出才能通过 */
function stashMockRoutes() {
  if (!existsSync(API_DIR)) return false
  mkdirSync(dirname(STASH_DIR), { recursive: true })
  rmSync(STASH_DIR, { recursive: true, force: true })
  renameSync(API_DIR, STASH_DIR)
  console.log('[desktop] 已临时移出 src/app/api（Mock 路由，静态导出不支持）')
  return true
}

function restoreMockRoutes() {
  if (!existsSync(STASH_DIR)) return
  renameSync(STASH_DIR, API_DIR)
  rmSync(dirname(STASH_DIR), { recursive: true, force: true })
  console.log('[desktop] 已还原 src/app/api')
}

const stashed = stashMockRoutes()
try {
  // 直接调 next 的入口脚本：Windows 上 execFileSync 无法直接 spawn .cmd（EINVAL）
  const nextBin = join(ROOT, 'node_modules', 'next', 'dist', 'bin', 'next')
  execFileSync(process.execPath, [nextBin, 'build'], {
    cwd: ROOT,
    stdio: 'inherit',
    env: {
      ...process.env,
      BUILD_TARGET: 'desktop',
      NEXT_PUBLIC_USE_MOCK: 'false',
      NEXT_PUBLIC_API_BASE: apiBase,
      // 桌面端不注册 Service Worker（本地资源无需缓存），见 register-sw.tsx
      NEXT_PUBLIC_DESKTOP: 'true',
    },
  })
  console.log('[desktop] 静态导出完成：out/')
} finally {
  if (stashed) restoreMockRoutes()
}
