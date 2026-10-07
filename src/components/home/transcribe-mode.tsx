'use client'

import { Cloud, Cpu, Loader2, ShieldAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useTaskStore } from '@/stores/task-store'
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
 *  云端选中时展示隐私提示（锁定项）；Mock 模式下附「无实际效果」小字（仅 Mock 显示）；
 *  云端能力探测中/未配置时禁用云端选项（M2-P2-A）。 */
export function TranscribeMode({ value, onChange }: TranscribeModeProps) {
  const capabilities = useTaskStore((s) => s.capabilities)
  const detecting = capabilities === null
  const cloudUnconfigured = capabilities?.cloudAsrConfigured === false
  const cloudDisabled = detecting || cloudUnconfigured

  return (
    <div className="flex flex-col gap-2" data-testid="transcribe-mode">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="text-sm font-medium">转写模式</span>
        <div className="flex gap-2" role="group" aria-label="转写模式">
          {OPTIONS.map(({ value: option, label, description, icon: Icon }) => {
            const isCloud = option === 'cloud'
            const disabled = isCloud && cloudDisabled
            return (
              <Button
                key={option}
                type="button"
                size="sm"
                variant={value === option ? 'default' : 'outline'}
                aria-pressed={value === option}
                title={description}
                disabled={disabled}
                onClick={() => onChange(option)}
                className="rounded-lg gap-1.5"
              >
                {isCloud && detecting ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden />
                ) : (
                  <Icon className="size-3.5" aria-hidden />
                )}
                {label}
              </Button>
            )
          })}
        </div>
      </div>

      {detecting ? (
        <p data-testid="cloud-capability-hint" className="text-xs text-muted-foreground">
          云端模式检测中…
        </p>
      ) : cloudUnconfigured ? (
        <p data-testid="cloud-capability-hint" className="text-xs text-amber-600 dark:text-amber-500">
          未配置云端 Key（CLOUD_ASR_API_KEY），云端模式不可用：请参考 README 或运行 setup.bat 配置
        </p>
      ) : null}

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
