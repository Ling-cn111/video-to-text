/**
 * 热词（ASR 引导词）的解析与格式化。
 *
 * 用途：把用户随手敲的一串专有名词，变成后端 `StartTranscribeRequest.hotwords`
 * 要的 `list[str]`。后端把它们和视频标题拼成 `initial_prompt`
 * （见 backend/app/services/asr.py 的 `build_initial_prompt`），引导 ASR 在识别时
 * 偏向这些写法——人名、产品名、专业术语这类「听得出音但猜不对字」的词收益最大。
 *
 * 为什么限制条数与单条长度：后端 `_INITIAL_PROMPT_MAX_CHARS = 200`，提示词超长会被
 * 直接截断，热词越多每个能分到的注意力越少，而且标题也要占位置。宁可让用户
 * 少填几个真正重要的词，也不要塞进 50 个把标题挤掉。
 */

/** 单个热词上限：超过这个长度基本是一句话，不是术语，引导意义不大 */
export const MAX_HOTWORD_CHARS = 20
/** 条数上限：再多后端提示词会被截断，实际起作用的是靠前的几个 */
export const MAX_HOTWORDS = 12
/**
 * 所有热词拼起来的上限（不含标题）。
 *
 * 200 是「热词 + 标题」的总预算，标题常见的 20-40 字要留出位置，
 * 所以热词侧只给 100。
 */
export const MAX_HOTWORDS_TOTAL_CHARS = 100

/** 分隔符：中英文逗号、顿号、分号、空白（含换行）都当分隔 */
const SEPARATORS = /[，,、;；\s]+/

/**
 * 把用户输入拆成热词数组。
 *
 * 去重按「忽略大小写的完全相等」判断：`Transformer` 与 `transformer` 算同一个，
 * 但 `Transformer` 与 `Transformers` 是两个词（复数可能是另一个产品名）。
 * 顺序保留用户输入的顺序 —— 后端的提示词是按顺序拼的，靠前的权重更靠前。
 */
export function parseHotwords(text: string | null | undefined): string[] {
  if (!text) return []
  const seen = new Set<string>()
  const result: string[] = []
  let used = 0
  for (const raw of text.split(SEPARATORS)) {
    const word = raw.trim().slice(0, MAX_HOTWORD_CHARS)
    if (!word) continue
    const key = word.toLowerCase()
    if (seen.has(key)) continue
    // +1 是拼接时那个分隔符的宽度
    if (used + word.length + 1 > MAX_HOTWORDS_TOTAL_CHARS) break
    seen.add(key)
    result.push(word)
    used += word.length + 1
    if (result.length >= MAX_HOTWORDS) break
  }
  return result
}

/** 反向：把数组拼回输入框里的可编辑文本（目前只在测试与调试里用） */
export function formatHotwords(words: string[] | null | undefined): string {
  return (words ?? []).join('、')
}
