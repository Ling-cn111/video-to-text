'use client'

import { Cloud, Cpu, ShieldAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { TranscribeEngine } from '@/lib/types'

interface TranscribeModeProps {
  value: TranscribeEngine
  onChange: (engine: TranscribeEngine) => void
}

const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK !== 'false'

const OPTIONS: Array<{ value: TranscribeEngine; label: string; description: string; icon: typeof Cloud }> = [
  { value: 'local', label: '本地', description: 'faster-whisper 本地推理', icon: Cpu },
  { value: 'cloud', label: '云端', description: 'OpenAI 兼容云端接口', icon: Cloud },
]

/** 首页转写模式选择：本地（默认）/ 云端，纯手动切换。
 *  云端选中时展示隐私提示（锁定项）；Mock 模式下附「无实际效果」小字（仅 Mock 显示）。 */
export function TranscribeMode({ value, onChange }: TranscribeModeProps) {
  return (
    <div className="flex flex-col gap-2" data-testid="transcribe-mode">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="text-sm font-medium">转写模式</span>
        <div className="flex gap-2" role="group" aria-label="转写模式">
          {OPTIONS.map(({ value: option, label, description, icon: Icon }) => (
            <Button
              key={option}
              type="button"
              size="sm"
              variant={value === option ? 'default' : 'outline'}
              aria-pressed={value === option}
              title={description}
              onClick={() => onChange(option)}
              className="rounded-lg gap-1.5"
            >
              <Icon className="size-3.5" aria-hidden />
              {label}
            </Button>
          ))}
        </div>
      </div>

      {value === 'cloud' && (
        <p
          data-testid="cloud-privacy-notice"
          className="flex items-start gap-1.5 text-xs leading-relaxed text-amber-600 dark:text-amber-500"
        >
          <ShieldAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          云端模式：音频将上传至第三方云端服务进行识别，请注意隐私。
        </p>
      )}

      {USE_MOCK && (
        <p data-testid="mock-no-effect-hint" className="text-xs text-muted-foreground">
          当前为 Mock 演示模式，此选择无实际效果。
        </p>
      )}
    </div>
  )
}
