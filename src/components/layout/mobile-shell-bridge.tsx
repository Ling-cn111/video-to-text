'use client'

import { useAndroidBack } from '@/hooks/use-android-back'
import { useShareIntent } from '@/hooks/use-share-intent'

/**
 * 移动端壳的全局原生适配层（Android 物理返回键、系统分享接收）。
 *
 * 放在根布局而不是各页面：返回键与分享入口都是全局行为，逐页注册会漏掉
 * /processing、/result 这类直链入口。非移动端构建下两个 hook 内部直接 return，
 * 不产生任何监听与动态 import。
 */
export function MobileShellBridge() {
  useAndroidBack()
  useShareIntent()
  return null
}
