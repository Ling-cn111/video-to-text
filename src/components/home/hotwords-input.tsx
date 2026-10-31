'use client'

import { useState } from 'react'
import { ChevronDown, Tag } from 'lucide-react'
import { useTaskStore } from '@/stores/task-store'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import {
  formatHotwords,
  MAX_HOTWORD_CHARS,
  MAX_HOTWORDS,
  MAX_HOTWORDS_TOTAL_CHARS,
  parseHotwords,
} from '@/lib/hotwords'

/**
 * 首页「热词」（ASR 引导词）输入。
 *
 * 为什么默认收起：这是一项**只在识别质量不好时才想起来**的功能。把它摊开在
 * 首屏会让「粘贴链接 → 点解析」这条主路径多一个必读区块；收成一行可展开的
 * 说明，普通用户可以直接忽略。
 *
 * 为什么用普通 Input 而不是标签化的 token 编辑器：要支持手机端（虚拟键盘、
 * 中文输入法的候选词状态）。自造 token 编辑器在 IME 组合输入时很容易吞键，
 * 而这里只需要「一串用逗号隔开的词」，原生 input 最稳。
 */
export function HotwordsInput() {
  const hotwordsText = useTaskStore((state) => state.hotwordsText)
  const setHotwords = useTaskStore((state) => state.setHotwords)
  const [expanded, setExpanded] = useState(false)

  const parsed = parseHotwords(hotwordsText)
  const hasWords = parsed.length > 0
  // 默认收起，但用户填过词就保持展开：下次进来还得改
  const open = expanded || hasWords

  return (
    <div className="flex flex-col gap-2" data-testid="hotwords-row">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={open}
        data-testid="hotwords-toggle"
        className="inline-flex w-fit items-center gap-1.5 rounded-full border bg-card px-3 py-1.5 text-sm transition-colors hover:bg-accent"
      >
        <Tag className="size-3.5 text-primary" aria-hidden />
        <span className="font-medium">热词</span>
        <span className="text-xs text-muted-foreground">
          {hasWords ? `已填 ${parsed.length} 个` : '可选，帮识别认准专有名词'}
        </span>
        <ChevronDown
          className={cn('size-3.5 text-muted-foreground transition-transform', open && 'rotate-180')}
          aria-hidden
        />
      </button>

      {open ? (
        <div className="flex flex-col gap-1.5 pl-1">
          <Input
            value={hotwordsText}
            onChange={(event) => setHotwords(event.target.value)}
            placeholder="如：Transformer、字节跳动、LangChain（逗号或顿号分隔）"
            aria-label="热词"
            data-testid="hotwords-input"
            className="h-10 rounded-lg text-sm"
            autoComplete="off"
          />
          <p className="text-xs text-muted-foreground" data-testid="hotwords-hint">
            {hasWords ? (
              <>
                识别时优先按这些写法（{formatHotwords(parsed)}）。
                {parsed.length >= MAX_HOTWORDS
                  ? `已达 ${MAX_HOTWORDS} 个上限，多的会被忽略。`
                  : `单条不超过 ${MAX_HOTWORD_CHARS} 字，最多 ${MAX_HOTWORDS} 个。`}
              </>
            ) : (
              <>
                人名、产品名、专业术语这类「听得出音但容易猜错字」的词最有用。
                最多 {MAX_HOTWORDS} 个，合计不超过 {MAX_HOTWORDS_TOTAL_CHARS} 字。
              </>
            )}
          </p>
        </div>
      ) : null}
    </div>
  )
}
