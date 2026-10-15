'use client'

import { useEffect, useRef, useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { copyTextToClipboard } from '@/lib/clipboard'

interface CopyFullTextButtonProps {
  /** 与「全文阅读」视图同源的段落（formatTranscriptToArticle 派生，无时间戳） */
  paragraphs: string[]
}

/** 复制成功反馈时长（毫秒）：与时间戳定位视图的复制反馈保持一致 */
const COPIED_FEEDBACK_MS = 1500

/**
 * 复制全文按钮：把「全文阅读」正文（无时间戳纯文本）写入剪贴板。
 * 内容与页面全文视图逐段一致（paragraphs.join('\n\n')），与 TXT / MD 导出同源（formatTranscriptToArticle）。
 * 成功：toast「已复制全文」+ 按钮短时变为「已复制」；失败：toast 错误提示（严禁静默）。
 */
export function CopyFullTextButton({ paragraphs }: CopyFullTextButtonProps) {
  const [copied, setCopied] = useState(false)
  const [copying, setCopying] = useState(false)
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 卸载时清理未触发的反馈复位定时器
  useEffect(
    () => () => {
      if (resetTimer.current) clearTimeout(resetTimer.current)
    },
    [],
  )

  const handleCopy = async () => {
    if (copying) return
    setCopying(true)
    const ok = await copyTextToClipboard(paragraphs.join('\n\n'))
    setCopying(false)

    if (!ok) {
      toast.error('复制失败，请手动选择正文复制')
      return
    }

    setCopied(true)
    toast.success('已复制全文')
    if (resetTimer.current) clearTimeout(resetTimer.current)
    resetTimer.current = setTimeout(() => setCopied(false), COPIED_FEEDBACK_MS)
  }

  return (
    <Button
      variant="outline"
      size="sm"
      className="gap-1.5"
      data-testid="copy-fulltext-button"
      onClick={handleCopy}
      disabled={paragraphs.length === 0 || copying}
      title="复制全文正文（纯文本，不含时间戳）"
    >
      {copied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
      {copied ? '已复制' : '复制全文'}
    </Button>
  )
}
