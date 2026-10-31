'use client'

import { useEffect, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { toast } from 'sonner'
import { IS_MOBILE_SHELL } from '@/lib/runtime'
import { processingHref } from '@/lib/routes'
import { shouldIgnoreShare, sharedTextToVideoInput, type SharePayload } from '@/lib/share-intent'
import { useTaskStore } from '@/stores/task-store'

/**
 * 接收 Android「分享」过来的文本并直接开转（移动端壳内启用）。
 *
 * ## 交互决策：为什么不先填进输入框让用户点一下
 *
 * 用户从 B站 点「分享 → 视频转文字」，意图已经明确到不能再明确。
 * 再让他回首页、看一眼输入框、点「开始转写」，是把一次点击拆成三次。
 * 所以这里直接提交并跳到进度页。
 *
 * 但**认不出链接时不提交**：分享来的可能是任意 text/plain（一段聊天记录、
 * 一条商品链接），静默失败或提交垃圾都会让用户困惑。认不出就退回首页并
 * 说明原因，用户还能手动粘贴。
 *
 * ## 原生侧对应实现
 *
 * `android/app/src/main/java/com/videototext/app/ShareIntentPlugin.java`，
 * 事件名 `share`，payload `{ text, subject? }`。冷启动由
 * `BridgeActivity.load()` 末尾的 `onNewIntent` 派发，事件带 retain，
 * JS 晚注册也会补发。
 */
export function useShareIntent(): void {
  const router = useRouter()
  const busyRef = useRef(false)
  const lastShareRef = useRef<{ text: string; at: number } | null>(null)

  useEffect(() => {
    if (!IS_MOBILE_SHELL) return

    let remove: (() => void) | undefined
    let disposed = false

    void (async () => {
      try {
        const { registerPlugin } = await import('@capacitor/core')
        const ShareIntent = registerPlugin<{
          addListener: (
            event: 'share',
            cb: (payload: SharePayload) => void,
          ) => Promise<{ remove: () => Promise<void> }>
        }>('ShareIntent')

        const handle = await ShareIntent.addListener('share', (payload) => {
          void handleShare(payload)
        })
        if (disposed) {
          void handle.remove()
          return
        }
        remove = () => void handle.remove()
      } catch (error) {
        // 非 Capacitor 环境（浏览器里跑 mobile 构建做冒烟测试）会抛错，忽略即可
        console.warn('[mobile] 分享意图监听未注册', error)
      }
    })()

    async function handleShare(payload: SharePayload) {
      const now = Date.now()
      if (shouldIgnoreShare(payload, lastShareRef.current, now)) return
      lastShareRef.current = { text: (payload.text ?? '').trim(), at: now }

      // 上一次提交还没回来时再来一条分享：直接忽略，避免两个任务并行跑
      if (busyRef.current) return
      busyRef.current = true
      try {
        const input = sharedTextToVideoInput(payload.text)
        if (!input) {
          toast.error('没有在分享内容里找到视频链接，请回到首页手动粘贴')
          router.replace('/')
          return
        }

        const taskId = await useTaskStore.getState().submitUrl(input)
        if (taskId) {
          router.push(processingHref(taskId))
          return
        }
        const error = useTaskStore.getState().error
        toast.error(error?.message ?? '解析失败，请稍后重试')
        router.replace('/')
      } finally {
        busyRef.current = false
      }
    }

    return () => {
      disposed = true
      remove?.()
    }
  }, [router])
}
