/**
 * 用户自带 Key（BYOK）：本地保存与请求头构造。
 *
 * ## 为什么要这个功能
 *
 * 站点自带的 Key 是全站共用的——谁打开页面都能用，用了多少、被谁用掉无法区分。
 * 想让「各用各的额度」，唯一安全的做法是让用户把自己的 Key 放在**他自己的浏览器**
 * 里，随请求带上，后端按请求级覆盖站点配置。
 *
 * ## 为什么不把站点 Key 发给前端让用户「配置」
 *
 * 那等于把 Key 公开发布：任何人 F12 打开 Network 就能拿走，然后无限盗刷。
 * 所以这个面板里只能填**用户自己的** Key，站点 Key 永远不出后端。
 *
 * ## 存储位置与风险边界
 *
 * - 存 localStorage：桌面端（Tauri）与网页共用一套代码，sessionStorage 关标签页就丢，
 *   用户每次重开都要重填，实际没人会用。
 * - localStorage 的代价：同源脚本可读。本项目没有第三方脚本（CSP `script-src 'self'`），
 *   也没有 UGC 内容，XSS 面很小；即便如此也要在界面上明确告知，让用户自己决定。
 * - **只在浏览器本地**，不上传、不写日志、不进 URL、不进请求体。
 *
 * ## 传输方式
 *
 * 只走请求头（`X-Vtt-Llm-Key` / `X-Vtt-Asr-Key`），与后端
 * `backend/app/services/byok.py` 的常量必须逐字一致。
 * 不走请求体：请求体会被错误日志、`payload=` 打印、体积限制等一整套链路碰到。
 */

/** 与后端 services/byok.py 的 HEADER_LLM_KEY / HEADER_ASR_KEY 必须一致 */
export const HEADER_LLM_KEY = 'X-Vtt-Llm-Key'
export const HEADER_ASR_KEY = 'X-Vtt-Asr-Key'

/** localStorage 键名（偏好持久化统一前缀风格，见 task-store 的 autoSummary 等） */
export const BYOK_LLM_KEY = 'byokLlmKey'
export const BYOK_ASR_KEY = 'byokAsrKey'

/** 与后端 MAX_KEY_LENGTH 对齐 */
const MAX_KEY_LENGTH = 512

/** 服务商与取 Key 的地址（面板里给链接，避免用户去搜到山寨站） */
export interface KeyProvider {
  id: string
  name: string
  /** 这个 Key 用在哪 */
  usage: 'llm' | 'asr'
  hint: string
  url: string
}

export const LLM_KEY_PROVIDERS: KeyProvider[] = [
  {
    id: 'deepseek',
    name: 'DeepSeek',
    usage: 'llm',
    hint: '以 sk- 开头，在「API keys」页面创建',
    url: 'https://platform.deepseek.com/api_keys',
  },
  {
    id: 'dashscope',
    name: '阿里云百炼（通义千问）',
    usage: 'llm',
    hint: '以 sk- 开头，在「API-KEY 管理」创建',
    url: 'https://bailian.console.aliyun.com/?apiKey=1',
  },
]

export const ASR_KEY_PROVIDERS: KeyProvider[] = [
  {
    id: 'siliconflow',
    name: '硅基流动',
    usage: 'asr',
    hint: '以 sk- 开头，在「API 密钥」页面创建',
    url: 'https://cloud.siliconflow.cn/account/ak',
  },
]

/** 读取结果：null 表示没填（或存了非法值） */
function readKey(storageKey: string): string | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(storageKey)
    if (!raw) return null
    const trimmed = raw.trim()
    return trimmed && trimmed.length <= MAX_KEY_LENGTH ? trimmed : null
  } catch {
    // 隐私模式 / 存储被禁用：当作没填，功能降级为「用站点 Key」
    return null
  }
}

function writeKey(storageKey: string, value: string | null): void {
  if (typeof window === 'undefined') return
  try {
    const trimmed = (value ?? '').trim()
    if (!trimmed) {
      window.localStorage.removeItem(storageKey)
      return
    }
    window.localStorage.setItem(storageKey, trimmed.slice(0, MAX_KEY_LENGTH))
  } catch {
    // 写失败（配额满 / 隐私模式）静默：本次仍可用，只是刷新后要重填
  }
}

export function readLlmKey(): string | null {
  return readKey(BYOK_LLM_KEY)
}

export function readAsrKey(): string | null {
  return readKey(BYOK_ASR_KEY)
}

export function saveLlmKey(value: string | null): void {
  writeKey(BYOK_LLM_KEY, value)
}

export function saveAsrKey(value: string | null): void {
  writeKey(BYOK_ASR_KEY, value)
}

export function clearAllKeys(): void {
  writeKey(BYOK_LLM_KEY, null)
  writeKey(BYOK_ASR_KEY, null)
}

/**
 * 请求头片段：没填就返回空对象，调用方直接展开即可。
 *
 * 为什么每次请求现读 localStorage 而不缓存到模块变量：用户可能开两个标签页，
 * 在其中一个改了 Key；缓存会让另一个标签页继续用旧 Key 发请求，然后拿到
 * 「Key 无效」却看到设置里明明是对的。
 */
export function byokHeaders(): Record<string, string> {
  const headers: Record<string, string> = {}
  const llm = readLlmKey()
  const asr = readAsrKey()
  if (llm) headers[HEADER_LLM_KEY] = llm
  if (asr) headers[HEADER_ASR_KEY] = asr
  return headers
}

/** 只带某一个 Key（总结只需要 LLM Key，别把 ASR Key 也捎上） */
export function byokHeaderFor(kind: 'llm' | 'asr'): Record<string, string> {
  const key = kind === 'llm' ? readLlmKey() : readAsrKey()
  if (!key) return {}
  return { [kind === 'llm' ? HEADER_LLM_KEY : HEADER_ASR_KEY]: key }
}

/** 界面展示用的掩码：只露末 4 位，且不足以反推全文 */
export function maskKey(key: string): string {
  const trimmed = key.trim()
  if (trimmed.length <= 8) return '••••'
  return `••••${trimmed.slice(-4)}`
}

/**
 * 本地格式校验（**不判断 Key 是否有效**，那只能由服务商回答）。
 *
 * 只拦明显的手误：空、含空格/换行（从文档复制常带）、超长。不校验 `sk-` 前缀：
 * 各家格式不统一，猜错会把合法 Key 拒掉，比放过去更糟。
 */
export function validateKeyFormat(raw: string): { ok: true; value: string } | { ok: false; reason: string } {
  const value = raw.trim()
  if (!value) return { ok: false, reason: '请填写 Key' }
  if (/\s/.test(value)) return { ok: false, reason: 'Key 里不能有空格或换行，请检查是否复制多了' }
  if (value.length > MAX_KEY_LENGTH) return { ok: false, reason: `Key 过长（超过 ${MAX_KEY_LENGTH} 字符）` }
  if (value.length < 8) return { ok: false, reason: 'Key 看起来太短了，请确认是否复制完整' }
  return { ok: true, value }
}
