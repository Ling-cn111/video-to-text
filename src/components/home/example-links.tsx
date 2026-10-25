'use client'

import { ExternalLink } from 'lucide-react'
import { PlatformBadge, UnsupportedBadge } from '@/components/shared/platform-badge'
import { SUPPORTED_PLATFORMS } from '@/lib/platforms/registry'
import type { Platform } from '@/lib/types'

export interface ExampleLink {
  label: string
  url: string
  platform: Platform | 'unsupported' | 'fail'
  hint?: string
}

/** 各平台的示例链接：只列当前构建真能转写的平台，由 SUPPORTED_PLATFORMS 决定 */
const PLATFORM_EXAMPLES: Record<Platform, Omit<ExampleLink, 'platform'>> = {
  bilibili: {
    label: 'B站视频示例',
    url: 'https://www.bilibili.com/video/BV1GJ411x7h7',
  },
  douyin: {
    label: '抖音视频示例',
    url: 'https://www.douyin.com/video/7301234567890123456',
  },
}

/** 固定的两个「演示异常」入口：与平台支持范围无关，任何构建都保留 */
const DEMO_EXAMPLES: ExampleLink[] = [
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

/**
 * 示例链接清单：平台示例跟随 SUPPORTED_PLATFORMS。
 *
 * 为什么不再写死四条：真实后端只接了 B站，写死的「抖音视频示例」点下去必然
 * 422 失败 —— 把用户的第一次点击变成一次报错，是最差的首次体验。示例链接
 * 的职责是让用户「一按就能看到成品」，不是展示我们认得多少域名。
 */
export const EXAMPLE_LINKS: ExampleLink[] = [
  ...SUPPORTED_PLATFORMS.map((platform) => ({
    ...PLATFORM_EXAMPLES[platform],
    platform,
  })),
  ...DEMO_EXAMPLES,
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
          className="group inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-full border bg-card px-3 py-1.5 text-sm transition-colors hover:border-primary/40 hover:bg-accent"
        >
          {example.platform === 'unsupported' ? (
            <UnsupportedBadge className="border-0 bg-transparent p-0" />
          ) : (
            <PlatformBadge
              platform={example.platform === 'fail' ? 'bilibili' : example.platform}
              className="border-0 bg-transparent p-0"
            />
          )}
          <span className="font-medium">{example.label}</span>
          {example.hint ? (
            <span className="whitespace-nowrap text-xs text-muted-foreground">（{example.hint}）</span>
          ) : null}
          <ExternalLink className="size-3 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
        </button>
      ))}
    </div>
  )
}
