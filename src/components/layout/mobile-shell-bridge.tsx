'use client'

import { useAndroidBack } from '@/hooks/use-android-back'

/**
 * 移动端壳的全局原生适配层（Android 物理返回键等）。
 *
 * 放在根布局而不是各页面：返回键是全局行为，逐页注册会漏掉
 * /processing、/result 这类直链入口。非移动端构建下 hook 内部直接 return，
 * 不产生任何监听与动态 import。
 */
export function MobileShellBridge() {
  useAndroidBack()
  return null
}
