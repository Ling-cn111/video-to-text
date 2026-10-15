/**
 * 剪贴板写入工具：优先使用异步 Clipboard API（navigator.clipboard.writeText），
 * 在非安全上下文（http://IP）或权限被拒导致其不可用时，降级为
 * 临时 textarea + document.execCommand('copy')。
 *
 * 返回是否真正写入成功，由调用方决定成功提示 / 失败提示（严禁静默失败）。
 */

/** 把文本写入剪贴板，成功返回 true；两条路径都失败返回 false */
export async function copyTextToClipboard(text: string): Promise<boolean> {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 权限被拒 / 非安全上下文：继续走降级路径
    }
  }
  return copyWithExecCommand(text)
}

/** 降级路径：隐藏 textarea + execCommand（兼容旧浏览器与 http 场景） */
function copyWithExecCommand(text: string): boolean {
  if (typeof document === 'undefined') return false

  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.top = '-9999px'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)

  try {
    textarea.select()
    textarea.setSelectionRange(0, text.length) // iOS Safari 需要显式选区
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    textarea.remove()
  }
}
