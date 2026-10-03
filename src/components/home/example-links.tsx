'use client'

import { ExternalLink } from 'lucide-react'
import { PlatformBadge, UnsupportedBadge } from '@/components/shared/platform-badge'

export interface ExampleLink {
  label: string
  url: string
  platform: 'bilibili' | 'douyin' | 'unsupported' | 'fail'
  hint?: string
}

export const EXAMPLE_LINKS: ExampleLink[] = [
  {
    label: 'B站视频示例',
    url: 'https://www.bilibili.com/video/BV1GJ411x7h7',
    platform: 'bilibili',
  },
  {
    label: '抖音视频示例',
    url: 'https://www.douyin.com/video/7301234567890123456',
    platform: 'douyin',
  },
  {
    label: '不支持平台演示',
    url: 'https://v.qq.com/x/cover/mzc00200xxxx.html',
    platform: 'unsupported',
    hint: '演示错误提示',
  },
  {
    label: '转写失败演示',
    url: 'https://www.bilibili.com/video/BV1FailDemo',
    platform: 'fail',
    hint: '演示失败与重试',
  },
]

interface ExampleLinksProps {
  onPick: (url: string) => void
}

/** 示例链接：一键填充输入框 */
export function ExampleLinks({ onPick }: ExampleLinksProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-sm text-muted-foreground">试试示例链接：</span>
      {EXAMPLE_LINKS.map((example) => (
        <button
          key={example.url}
          type="button"
          onClick={() => onPick(example.url)}
          title={example.url}
          className="group inline-flex items-center gap-2 rounded-full border bg-card px-3 py-1.5 text-sm transition-colors hover:border-primary/40 hover:bg-accent"
        >
          {example.platform === 'bilibili' || example.platform === 'douyin' ? (
            <PlatformBadge platform={example.platform} className="border-0 bg-transparent p-0" />
          ) : (
            <UnsupportedBadge className="border-0 bg-transparent p-0" />
          )}
          <span className="font-medium">{example.label}</span>
          {example.hint ? (
            <span className="text-xs text-muted-foreground">（{example.hint}）</span>
          ) : null}
          <ExternalLink className="size-3 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
        </button>
      ))}
    </div>
  )
}
