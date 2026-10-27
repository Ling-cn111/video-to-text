/**
 * 用户自带配置（BYOK）：本地保存与请求构造。
 *
 * ## 为什么要这个功能
 *
 * 站点自带的 Key 是全站共用的——谁打开页面都能用，用了多少、被谁用掉无法区分。
 * 想让「各用各的额度」，唯一安全的做法是让用户把自己的配置放在**他自己的浏览器**
 * 里，随请求带上，后端按请求级覆盖站点配置。
 *
 * ## 为什么连接口地址和模型也要让用户填
 *
 * 只填 Key 是不够的：Key 属于某个服务商，而请求要打到那个服务商的地址、用那个
 * 服务商的模型名。旧实现把地址与模型钉死在后端的 `LLM_PRESETS` 里，于是用户
 * 填了 OpenAI / Moonshot / 智谱的 Key，后端仍拿它去请求 DeepSeek —— 必然 401。
 * 这就是「别家的 Key 用不了」的根因。三项必须一起配置才叫「支持任意服务商」。
 *
 * ## 为什么不把站点 Key 发给前端让用户「配置」
 *
 * 那等于把 Key 公开发布：任何人 F12 打开 Network 就能拿走，然后无限盗刷。
 * 所以这个面板里只能填**用户自己的**配置，站点 Key 永远不出后端。
 *
 * ## 只收 LLM 配置，不收 ASR 配置
 *
 * 云端 ASR 用的是硅基流动的免费模型（`XingChenAGI/XingChenASR-V3.2-Ultra` 等，
 * 刊例价 0 元/千字符），用户没有自备 Key 的必要；多一组输入框只会多一道门槛。
 * 后端仍保留 `X-Vtt-Asr-Key` 支持（脚本 / API 调用方可用），界面不再收集。
 *
 * ## 存储位置与风险边界
 *
 * - 存 localStorage：桌面端（Tauri）与网页共用一套代码，sessionStorage 关标签页就丢，
 *   用户每次重开都要重填，实际没人会用。
 * - localStorage 的代价：同源脚本可读。本项目没有第三方脚本（CSP `script-src 'self'`），
 *   也没有 UGC 内容，XSS 面很小；即便如此也要在界面上明确告知，让用户自己决定。
 * - **Key 会经过服务器**：浏览器把 Key 放进请求头发给后端，后端用它去调服务商，
 *   用完即弃、不落库、不写日志。但「不落库」不等于「部署者看不到」——技术上
 *   部署者可以在进程里打印任何东西。这一点必须在界面上如实说，不能写「不上传」。
 *
 * ## 传输方式
 *
 * - Key 只走请求头（`X-Vtt-Llm-Key`），与后端
 *   `backend/app/services/byok.py` 的常量必须逐字一致。
 *   不走请求体：请求体会被错误日志、`payload=` 打印、体积限制等一整套链路碰到。
 * - 接口地址与模型走**请求体**（`llmBaseUrl` / `llmModel`）：它们不是秘密，
 *   放请求体才能让后端用 pydantic 校验并给出可读的错误。后端仍会对地址做
 *   SSRF 校验（环回 / 私有 / 保留地址一律拒绝）。
 */

/** 与后端 services/byok.py 的 HEADER_LLM_KEY / HEADER_ASR_KEY 必须一致 */
export const HEADER_LLM_KEY = 'X-Vtt-Llm-Key'
/** 后端仍支持（脚本 / API 调用方），界面已不再收集：云端 ASR 是免费模型 */
export const HEADER_ASR_KEY = 'X-Vtt-Asr-Key'

/** localStorage 键名（偏好持久化统一前缀风格，见 task-store 的 autoSummary 等） */
export const BYOK_LLM_KEY = 'byokLlmKey'
export const BYOK_LLM_BASE_URL = 'byokLlmBaseUrl'
export const BYOK_LLM_MODEL = 'byokLlmModel'

/** 与后端 MAX_KEY_LENGTH / MAX_BASE_URL_LENGTH / MAX_MODEL_LENGTH 对齐 */
const MAX_KEY_LENGTH = 512
const MAX_BASE_URL_LENGTH = 512
const MAX_MODEL_LENGTH = 128

/**
 * 服务商预设：选一个就把地址和常用模型填好，用户仍可改成任意值。
 *
 * 为什么要有预设：让用户手打 `https://dashscope.aliyuncs.com/compatible-mode/v1`
 * 不现实，写错一个字符就得到一个看不懂的连接错误。预设只是「填空助手」，
 * 不是白名单——真正的校验在后端（地址过 SSRF、模型交给服务商判断）。
 */
export interface LlmProviderPreset {
  id: string
  name: string
  /** OpenAI 兼容接口的 base_url（不含 /chat/completions） */
  baseUrl: string
  /** 该服务商常用的模型名，第一个作为默认填值 */
  models: string[]
  /** 去哪里取 Key（避免用户搜到山寨站） */
  keyUrl: string
  /** 取 Key 的简短提示 */
  keyHint: string
}

export const LLM_PROVIDER_PRESETS: LlmProviderPreset[] = [
  {
    id: 'deepseek',
    name: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com/v1',
    models: ['deepseek-chat', 'deepseek-reasoner'],
    keyUrl: 'https://platform.deepseek.com/api_keys',
    keyHint: '以 sk- 开头，在「API keys」页面创建',
  },
  {
    id: 'dashscope',
    name: '阿里云百炼（通义千问）',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    models: ['qwen-plus', 'qwen-turbo', 'qwen-max'],
    keyUrl: 'https://bailian.console.aliyun.com/?apiKey=1',
    keyHint: '以 sk- 开头，在「API-KEY 管理」创建',
  },
  {
    id: 'moonshot',
    name: 'Moonshot（Kimi）',
    baseUrl: 'https://api.moonshot.cn/v1',
    models: ['moonshot-v1-8k', 'moonshot-v1-32k', 'kimi-k2-0905-preview'],
    keyUrl: 'https://platform.moonshot.cn/console/api-keys',
    keyHint: '以 sk- 开头，在「API Key 管理」创建',
  },
  {
    id: 'zhipu',
    name: '智谱 GLM',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    models: ['glm-4-plus', 'glm-4-flash'],
    keyUrl: 'https://bigmodel.cn/usercenter/apikeys',
    keyHint: '在「API Keys」页面创建',
  },
  {
    id: 'siliconflow',
    name: '硅基流动',
    baseUrl: 'https://api.siliconflow.cn/v1',
    models: ['deepseek-ai/DeepSeek-V3', 'Qwen/Qwen2.5-72B-Instruct'],
    keyUrl: 'https://cloud.siliconflow.cn/account/ak',
    keyHint: '在「API 密钥」页面创建',
  },
  {
    id: 'openai',
    name: 'OpenAI',
    baseUrl: 'https://api.openai.com/v1',
    models: ['gpt-4o-mini', 'gpt-4o'],
    keyUrl: 'https://platform.openai.com/api-keys',
    keyHint: '以 sk- 开头，在「API keys」页面创建',
  },
  {
    id: 'custom',
    name: '其他 / 自建',
    baseUrl: '',
    models: [],
    keyUrl: '',
    keyHint: '任何兼容 OpenAI chat/completions 协议的服务都可以',
  },
]

/** 用户的完整 LLM 配置：三项都可为空（为空即回落站点配置） */
export interface LlmConfig {
  key: string | null
  baseUrl: string | null
  model: string | null
}

export const EMPTY_LLM_CONFIG: LlmConfig = { key: null, baseUrl: null, model: null }

/** 读取结果：null 表示没填（或存了非法值） */
function readStored(storageKey: string, maxLength: number): string | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(storageKey)
    if (!raw) return null
    const trimmed = raw.trim()
    return trimmed && trimmed.length <= maxLength ? trimmed : null
  } catch {
    // 隐私模式 / 存储被禁用：当作没填，功能降级为「用站点配置」
    return null
  }
}

function writeStored(storageKey: string, value: string | null, maxLength: number): void {
  if (typeof window === 'undefined') return
  try {
    const trimmed = (value ?? '').trim()
    if (!trimmed) {
      window.localStorage.removeItem(storageKey)
      return
    }
    window.localStorage.setItem(storageKey, trimmed.slice(0, maxLength))
  } catch {
    // 写失败（配额满 / 隐私模式）静默：本次仍可用，只是刷新后要重填
  }
}

export function readLlmKey(): string | null {
  return readStored(BYOK_LLM_KEY, MAX_KEY_LENGTH)
}

export function readLlmBaseUrl(): string | null {
  return readStored(BYOK_LLM_BASE_URL, MAX_BASE_URL_LENGTH)
}

export function readLlmModel(): string | null {
  return readStored(BYOK_LLM_MODEL, MAX_MODEL_LENGTH)
}

/** 一次性读全三项：调用方（设置面板 / 门禁判断）几乎总是同时需要它们 */
export function readLlmConfig(): LlmConfig {
  return {
    key: readLlmKey(),
    baseUrl: readLlmBaseUrl(),
    model: readLlmModel(),
  }
}

export function saveLlmConfig(config: Partial<LlmConfig>): void {
  if (config.key !== undefined) writeStored(BYOK_LLM_KEY, config.key, MAX_KEY_LENGTH)
  if (config.baseUrl !== undefined) writeStored(BYOK_LLM_BASE_URL, config.baseUrl, MAX_BASE_URL_LENGTH)
  if (config.model !== undefined) writeStored(BYOK_LLM_MODEL, config.model, MAX_MODEL_LENGTH)
}

export function saveLlmKey(value: string | null): void {
  saveLlmConfig({ key: value })
}

export function clearLlmConfig(): void {
  saveLlmConfig({ key: null, baseUrl: null, model: null })
}

/**
 * LLM Key 的请求头片段：没填就返回空对象，调用方直接展开即可。
 *
 * 为什么每次请求现读 localStorage 而不缓存到模块变量：用户可能开两个标签页，
 * 在其中一个改了配置；缓存会让另一个标签页继续用旧配置发请求，然后拿到
 * 「Key 无效」却看到设置里明明是对的。
 */
export function byokHeaderFor(): Record<string, string> {
  const key = readLlmKey()
  if (!key) return {}
  return { [HEADER_LLM_KEY]: key }
}

/**
 * LLM 请求体里的自定义配置片段：两项都没填时返回空对象，保持请求体形状不变
 * （老后端不认这两个字段，但也不会因此报错）。
 */
export function byokBodyFor(): { llmBaseUrl?: string; llmModel?: string } {
  const baseUrl = readLlmBaseUrl()
  const model = readLlmModel()
  const body: { llmBaseUrl?: string; llmModel?: string } = {}
  if (baseUrl) body.llmBaseUrl = baseUrl
  if (model) body.llmModel = model
  return body
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

/**
 * 接口地址的本地格式校验。
 *
 * 只做「看起来像不像一个 URL」这一层，真正的 SSRF 判定在后端（那里才有
 * 完整的私有网段表，且前端校验可以被绕过，不能当安全边界）。这里拦一下是为了
 * 让用户在保存时就知道写错了，而不是等一轮总结请求失败。
 */
export function validateBaseUrlFormat(raw: string): { ok: true; value: string } | { ok: false; reason: string } {
  const value = raw.trim()
  if (!value) return { ok: true, value: '' }
  if (/\s/.test(value)) return { ok: false, reason: '接口地址里不能有空格或换行' }
  if (value.length > MAX_BASE_URL_LENGTH) return { ok: false, reason: `接口地址过长（超过 ${MAX_BASE_URL_LENGTH} 字符）` }
  if (!/^https?:\/\//i.test(value)) return { ok: false, reason: '接口地址必须以 http:// 或 https:// 开头' }
  try {
    const parsed = new URL(value)
    if (!parsed.hostname) return { ok: false, reason: '接口地址缺少主机名' }
  } catch {
    return { ok: false, reason: '接口地址格式不正确' }
  }
  return { ok: true, value: value.replace(/\/+$/, '') }
}

/** 模型名的本地校验：只拦空白与超长，命名规范交给服务商 */
export function validateModelFormat(raw: string): { ok: true; value: string } | { ok: false; reason: string } {
  const value = raw.trim()
  if (!value) return { ok: true, value: '' }
  if (/\s/.test(value)) return { ok: false, reason: '模型名里不能有空格' }
  if (value.length > MAX_MODEL_LENGTH) return { ok: false, reason: `模型名过长（超过 ${MAX_MODEL_LENGTH} 字符）` }
  return { ok: true, value }
}

/**
 * 三项配置的完整性判断（**用于界面提示，不作为请求前的硬拦截**）。
 *
 * 规则：填了地址就必须填模型 —— 站点默认模型（如 `deepseek-flash`）在别家
 * 服务商那里不存在，发过去只会得到一句「model not found」。后端
 * `_resolve_provider` 也会拒绝这个组合，这里提前告诉用户少填了什么。
 */
export function llmConfigIssue(config: LlmConfig): string | null {
  if (config.baseUrl && !config.model) {
    return '填写了自定义接口地址，还需要填写模型名（例如 deepseek-chat / gpt-4o-mini）'
  }
  // 地址与 Key 必须同源：只填地址不填 Key，后端会回落到站点 Key 去打用户指定的
  // 地址 —— 等于把站点凭据送到第三方服务器。后端 `_resolve_provider` 也会拦，
  // 这里同步拦住是为了让用户在保存时就看到，而不是等一次失败的总结。
  if (config.baseUrl && !config.key) {
    return '填写了自定义接口地址，还需要填写该服务商的 API Key：站点不会把自己的 Key 发往你指定的地址'
  }
  return null
}
