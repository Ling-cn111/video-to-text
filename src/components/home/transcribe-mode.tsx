'use client'

import { Cloud, Cpu, Loader2, ShieldAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useTaskStore } from '@/stores/task-store'
import { cloudUnconfiguredMessage } from '@/lib/capability-hint'
import { useHostname } from '@/hooks/use-hostname'
import { useByokStatus } from '@/hooks/use-byok-status'
import type { TranscribeEngine } from '@/lib/types'

interface TranscribeModeProps {
  value: TranscribeEngine
  onChange: (engine: TranscribeEngine) => void
}

const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK !== 'false'

/**
 * 选项按钮的悬停说明。
 *
 * 只写用户能判断的东西（要不要钱、要不要联网、快不快），**不写实现细节**：
 * 旧文案是「faster-whisper 本地推理」「OpenAI 兼容云端接口」，普通用户看完
 * 仍然不知道该点哪个——这两个词是实现选型，不是选择依据。
 * 注意：按钮可见文案（本地 / 云端）与下方常显说明行属锁定项（docs/frontend-features.md §6），
 * 这里只改 `title` 悬停提示，不动可见文案。
 */
const OPTIONS: Array<{ value: TranscribeEngine; label: string; description: string; icon: typeof Cloud }> = [
  { value: 'local', label: '本地', description: '免费、不联网，在本机运行，速度较慢', icon: Cpu },
  { value: 'cloud', label: '云端', description: '更快、更准，需要联网，音频会上传至第三方', icon: Cloud },
]

/** 首页转写模式选择：本地（默认）/ 云端，纯手动切换。
 *  云端选中时展示隐私提示（锁定项）；Mock 模式下附「无实际效果」小字（仅 Mock 显示）；
 *  云端能力探测中/未配置时禁用云端选项（M2-P2-A）。 */
export function TranscribeMode({ value, onChange }: TranscribeModeProps) {
  const capabilities = useTaskStore((s) => s.capabilities)
  const hostname = useHostname()
  const byok = useByokStatus()
  const detecting = capabilities === null || !byok.ready
  // 站点没配**且**用户也没填自己的 Key 才真的用不了；用户填了 Key 就放行
  const cloudUnconfigured = capabilities?.cloudAsrConfigured === false && !byok.hasAsrKey
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
        // 文案分环境：本地给配置指引，公网只说「暂不可用 + 用哪个替代」（不暴露 Key 名）
        <p data-testid="cloud-capability-hint" className="text-xs text-amber-600 dark:text-amber-500">
          {hostname === null
            ? '云端模式暂不可用，可在右上角「API 设置」填写自己的 Key'
            : cloudUnconfiguredMessage(hostname)}
        </p>
      ) : null}

      {/* 说明行：常显、随选择变化；云端态合并隐私提示（amber + 盾牌图标）——M2-P2-B 锁定项 */}
      {value === 'local' ? (
        <p data-testid="mode-description" className="text-xs leading-relaxed text-muted-foreground">
          本地模式：免费 · 离线 · 较慢，准确率中等
        </p>
      ) : (
        <p
          data-testid="mode-description"
          className="flex items-start gap-1.5 text-xs leading-relaxed text-amber-600 dark:text-amber-500"
        >
          <ShieldAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          云端模式：更快 · 准确率更高 · 音频将上传至第三方服务
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
