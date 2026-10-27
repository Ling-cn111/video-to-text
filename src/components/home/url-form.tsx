'use client'

import { useRef, useState } from 'react'
import { toast } from 'sonner'
import { useRouter } from 'next/navigation'
import { Link2, Loader2, Wand2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useTaskStore } from '@/stores/task-store'
import { processingHref } from '@/lib/routes'
import { normalizeVideoInput, resolveVideoUrl } from '@/lib/platform'
import {
  supportedPlatformsShortText,
  unavailablePlatformMessage,
  unsupportedHostMessage,
} from '@/lib/platforms/registry'

interface UrlFormProps {
  url: string
  onUrlChange: (url: string) => void
}

/** 首页输入框 + 解析按钮：前端先做一层格式校验，再提交到任务状态机 */
export function UrlForm({ url, onUrlChange }: UrlFormProps) {
  const router = useRouter()
  const inputRef = useRef<HTMLInputElement>(null)
  const submitUrl = useTaskStore((s) => s.submitUrl)
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (submitting) return
    const trimmed = url.trim()

    if (!trimmed) {
      toast.warning('请先粘贴视频链接或视频号')
      inputRef.current?.focus()
      return
    }
    // 与 Mock 服务端同一套解析规则，非法 / 不支持平台直接拦截在本地
    const result = resolveVideoUrl(trimmed)
    if (!result.ok) {
      toast.error(
        result.reason === 'invalid'
          ? '链接格式不正确，请粘贴完整的视频页面链接，或直接输入 BV 号 / av 号'
          : result.reason === 'unavailable'
            ? unavailablePlatformMessage(result.platform)
            : unsupportedHostMessage(result.host),
      )
      return
    }

    setSubmitting(true)
    // 裸视频号在这里补成完整链接：后端只接受公网 http(s) URL（SSRF 前提），
    // 让后端放宽校验去接受裸号会削弱这条前提，补全放在前端更安全
    const taskId = await submitUrl(normalizeVideoInput(trimmed))
    setSubmitting(false)

    if (taskId) {
      router.push(processingHref(taskId))
      return
    }
    const error = useTaskStore.getState().error
    toast.error(error?.message ?? '解析失败，请稍后重试')
  }

  return (
    <form onSubmit={handleSubmit} className="flex w-full flex-col gap-3 sm:flex-row">
      <div className="relative flex-1">
        <Link2 className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input
          ref={inputRef}
          value={url}
          onChange={(event) => onUrlChange(event.target.value)}
          placeholder={`粘贴${supportedPlatformsShortText()}视频链接，或直接输入 BV 号 / av 号`}
          className="h-12 rounded-xl pl-9 text-sm sm:text-base"
          // 现在也接受裸视频号（BV1xx411c7mD / av170001），键盘不该只给 URL 布局
          inputMode="text"
          autoComplete="off"
          aria-label="视频链接"
          data-testid="video-url-input"
        />
      </div>
      <Button type="submit" size="lg" disabled={submitting} className="h-12 rounded-xl px-6 text-base font-medium">
        {submitting ? (
          <Loader2 className="size-4 animate-spin" aria-hidden />
        ) : (
          <Wand2 className="size-4" aria-hidden />
        )}
        {submitting ? '解析中…' : '解析'}
      </Button>
    </form>
  )
}
