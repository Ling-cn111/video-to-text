'use client'

import { Cloud, Cpu, ShieldAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useTaskStore } from '@/stores/task-store'
import { cloudUnconfiguredMessage } from '@/lib/capability-hint'
import { useHostname } from '@/hooks/use-hostname'
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
 */
const OPTIONS: Array<{ value: TranscribeEngine; label: string; description: string; icon: typeof Cloud }> = [
  { value: 'local', label: '本地', description: '免费、不联网，在本机运行，速度较慢', icon: Cpu },
  { value: 'cloud', label: '云端', description: '更快、更准，需要联网，音频会上传至第三方', icon: Cloud },
]

/**
 * 转写模式选择器：**只在「这个部署真能跑本地 ASR」时出现**。
 *
 * 为什么改成按部署隐藏：线上（Vercel + 阿里云 FC）镜像按 requirements-cloud.txt
 * 构建，根本没有 faster-whisper，本地引擎 import 就失败。把「本地 / 云端」摆给
 * 线上访客，等于给他一个必然失败的按钮，还要他先理解这两个词的区别。
 *
 * 判断依据是 `capabilities.localAsrAvailable`（后端探测依赖装没装），不是猜：
 * 本机自建、装了 faster-whisper 的用户仍会看到它，那时它才真的有意义；
 * 网页端与桌面端都连云端后端，都不显示，由后端按「字幕 → 云端 ASR → 本地」
 * 的可用性自动挑（见 backend/app/routers/transcribe.py 的 _resolve_engine）。
 *
 * Mock 模式下恒为真：演示站要能展示完整功能，锁定项 §6 的默认值与说明行也在那里。
 */
export function TranscribeMode({ value, onChange }: TranscribeModeProps) {
  const capabilities = useTaskStore((s) => s.capabilities)
  const hostname = useHostname()

  // 探测未完成或本机没有本地引擎 → 不渲染（用户不需要为部署形态做选择）
  if (!capabilities?.localAsrAvailable) return null

  // 有本地引擎说明是自建部署；云端能否用取决于站点有没有配 ASR Key
  // （云端 ASR 走的是站点 Key，界面不再收集用户自带的 ASR Key）
  const cloudUnconfigured = capabilities.cloudAsrConfigured === false

  return (
    <div className="flex flex-col gap-2" data-testid="transcribe-mode">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="text-sm font-medium">转写模式</span>
        <div className="flex gap-2" role="group" aria-label="转写模式">
          {OPTIONS.map(({ value: option, label, description, icon: Icon }) => {
            const isCloud = option === 'cloud'
            return (
              <Button
                key={option}
                type="button"
                size="sm"
                variant={value === option ? 'default' : 'outline'}
                aria-pressed={value === option}
                title={description}
                disabled={isCloud && cloudUnconfigured}
                onClick={() => onChange(option)}
                className="rounded-lg gap-1.5"
              >
                <Icon className="size-3.5" aria-hidden />
                {label}
              </Button>
            )
          })}
        </div>
      </div>

      {cloudUnconfigured ? (
        // 文案分环境：本地给配置指引，公网只说「暂不可用 + 用哪个替代」（不暴露 Key 名）
        <p data-testid="cloud-capability-hint" className="text-xs text-amber-600 dark:text-amber-500">
          {hostname === null ? '云端模式暂不可用，可先用本地模式' : cloudUnconfiguredMessage(hostname)}
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
