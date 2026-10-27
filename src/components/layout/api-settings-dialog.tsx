'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { ExternalLink, KeyRound, ShieldCheck, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useApiSettingsOpenSignal } from '@/hooks/use-api-settings'
import { notifyByokChanged } from '@/hooks/use-byok-status'
import {
  EMPTY_LLM_CONFIG,
  LLM_PROVIDER_PRESETS,
  maskKey,
  readLlmConfig,
  saveLlmConfig,
  validateBaseUrlFormat,
  validateKeyFormat,
  validateModelFormat,
  type LlmConfig,
  type LlmProviderPreset,
} from '@/lib/byok'

/**
 * 识别当前配置对应哪个预设：地址与模型都对得上才算。
 *
 * 为什么不存「用户选了哪个服务商」：服务商只是填空助手，真正生效的是地址与
 * 模型这两个值。存了反而会不一致——用户选了 DeepSeek 又把地址改成自己的代理，
 * 界面还显示 DeepSeek，会让人以为请求打到了官方。这里由值反推，永远不会骗人。
 */
function matchPreset(config: LlmConfig): LlmProviderPreset | null {
  if (!config.baseUrl) return null
  const normalized = config.baseUrl.replace(/\/+$/, '')
  return (
    LLM_PROVIDER_PRESETS.find((preset) => preset.baseUrl && preset.baseUrl.replace(/\/+$/, '') === normalized) ?? null
  )
}

/** 面板里的一行说明文字 */
function FieldHint({ children, testId }: { children: React.ReactNode; testId?: string }) {
  return (
    <p className="text-xs text-muted-foreground" data-testid={testId}>
      {children}
    </p>
  )
}

export interface ApiSettingsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ApiSettingsDialog({ open, onOpenChange }: ApiSettingsDialogProps) {
  // 初始为空配置，挂载后再读 localStorage：服务端没有 localStorage，
  // 直接在 useState 初值里读会导致 hydration 前后 DOM 不一致
  const [config, setConfig] = useState<LlmConfig>(EMPTY_LLM_CONFIG)
  // 草稿态：用户正在编辑但还没点保存。切到已保存态时清空。
  const [draftKey, setDraftKey] = useState('')
  const [draftBaseUrl, setDraftBaseUrl] = useState('')
  const [draftModel, setDraftModel] = useState('')
  const [editingKey, setEditingKey] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 任意位置（结果页总结卡、首页提示）都能把这个面板叫起来
  const handleExternalOpen = useCallback(() => onOpenChange(true), [onOpenChange])
  useApiSettingsOpenSignal(handleExternalOpen)

  useEffect(() => {
    if (!open) return
    const stored = readLlmConfig()
    setConfig(stored)
    setDraftBaseUrl(stored.baseUrl ?? '')
    setDraftModel(stored.model ?? '')
    setDraftKey('')
    setEditingKey(false)
    setError(null)
  }, [open])

  const activePreset = useMemo(() => matchPreset(config), [config])
  const keyUrl = activePreset?.keyUrl || 'https://platform.deepseek.com/api_keys'
  const keyUrlName = activePreset?.name || 'DeepSeek'

  function applyPreset(preset: LlmProviderPreset) {
    setError(null)
    // 选「其他 / 自建」时不清空用户已填的地址与模型：他多半是想继续改，
    // 清空反而要重新打一遍
    if (preset.id === 'custom') return
    setDraftBaseUrl(preset.baseUrl)
    setDraftModel(preset.models[0] ?? '')
  }

  function handleSave() {
    const keyResult = config.key && !editingKey ? { ok: true as const, value: config.key } : validateKeyFormat(draftKey)
    if (!keyResult.ok) {
      setError(keyResult.reason)
      return
    }
    const urlResult = validateBaseUrlFormat(draftBaseUrl)
    if (!urlResult.ok) {
      setError(urlResult.reason)
      return
    }
    const modelResult = validateModelFormat(draftModel)
    if (!modelResult.ok) {
      setError(modelResult.reason)
      return
    }
    // 填了地址却没填模型：站点默认模型（如 deepseek-flash）在别家服务商那里
    // 不存在，发过去只会得到一句「model not found」。这里就拦住。
    if (urlResult.value && !modelResult.value) {
      setError('填写了自定义接口地址，还需要填写模型名（例如 deepseek-chat / gpt-4o-mini）')
      return
    }

    const next: LlmConfig = {
      key: keyResult.value,
      baseUrl: urlResult.value || null,
      model: modelResult.value || null,
    }
    saveLlmConfig(next)
    setConfig(next)
    setDraftKey('')
    setEditingKey(false)
    setError(null)
    // 通知同页的能力门禁重算：刚填了配置，结果页那个灰掉的「生成总结」应当立刻可用
    notifyByokChanged()
    toast.success('配置已保存，只存在这台设备的浏览器里')
  }

  function handleClear() {
    saveLlmConfig(EMPTY_LLM_CONFIG)
    setConfig(EMPTY_LLM_CONFIG)
    setDraftKey('')
    setDraftBaseUrl('')
    setDraftModel('')
    setEditingKey(false)
    setError(null)
    notifyByokChanged()
    toast.success('已清除 API 配置')
  }

  // 已保存且没在改 Key → 显示掩码；点「更换」才露出输入框
  const showingStoredKey = Boolean(config.key) && !editingKey
  const hasAnyConfig = Boolean(config.key || config.baseUrl || config.model)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-xl overflow-y-auto" data-testid="api-settings-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyRound className="size-4" aria-hidden />
            API 设置
          </DialogTitle>
          <DialogDescription>
            填自己的 AI 总结服务，就用你自己的额度，不占用站点配额。本站默认不提供总结
            Key，需要自己填。语音识别用的是免费模型，不需要填。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-2 rounded-lg border p-4" data-testid="byok-llm">
            <div className="flex items-start justify-between gap-3">
              <div className="space-y-0.5">
                <span className="text-sm font-medium">AI 总结服务</span>
                <FieldHint>兼容 OpenAI chat/completions 协议的服务商都支持</FieldHint>
              </div>
              {config.key ? (
                <span className="shrink-0 rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-600 dark:text-emerald-400">
                  已启用
                </span>
              ) : (
                <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                  未填写
                </span>
              )}
            </div>

            {/* 服务商预设：只是填空助手，不是白名单 */}
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">服务商</Label>
              <div className="flex flex-wrap gap-1.5" data-testid="byok-provider-presets">
                {LLM_PROVIDER_PRESETS.map((preset) => {
                  const active = preset.id === 'custom' ? !activePreset : activePreset?.id === preset.id
                  return (
                    <Button
                      key={preset.id}
                      type="button"
                      size="sm"
                      variant={active ? 'default' : 'outline'}
                      className="h-7 px-2 text-xs"
                      onClick={() => applyPreset(preset)}
                      data-testid={`byok-preset-${preset.id}`}
                      aria-pressed={active}
                    >
                      {preset.name}
                    </Button>
                  )
                })}
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="byok-base-url" className="text-xs text-muted-foreground">
                接口地址
              </Label>
              <Input
                id="byok-base-url"
                value={draftBaseUrl}
                onChange={(event) => {
                  setDraftBaseUrl(event.target.value)
                  setError(null)
                }}
                placeholder="https://api.deepseek.com/v1"
                autoComplete="off"
                spellCheck={false}
                className="font-mono text-xs"
                data-testid="byok-llm-base-url-input"
              />
              <FieldHint testId="byok-llm-base-url-hint">
                留空则使用本站配置的服务商。填自建 / 中转服务时注意：不要填本机或内网地址，后端会拒绝。
              </FieldHint>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="byok-model" className="text-xs text-muted-foreground">
                模型名
              </Label>
              <Input
                id="byok-model"
                value={draftModel}
                onChange={(event) => {
                  setDraftModel(event.target.value)
                  setError(null)
                }}
                placeholder="deepseek-chat"
                autoComplete="off"
                spellCheck={false}
                className="font-mono text-xs"
                data-testid="byok-llm-model-input"
              />
              <FieldHint>填了自定义接口地址时必须填。模型名以服务商的文档为准。</FieldHint>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="byok-llm" className="text-xs text-muted-foreground">
                API Key
              </Label>
              {showingStoredKey ? (
                <div className="flex items-center gap-2">
                  <code
                    className="flex-1 truncate rounded-md border bg-muted/40 px-3 py-1.5 font-mono text-sm"
                    data-testid="byok-llm-masked"
                  >
                    {maskKey(config.key as string)}
                  </code>
                  <Button type="button" variant="outline" size="sm" onClick={() => setEditingKey(true)}>
                    更换
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={handleClear}
                    data-testid="byok-llm-clear"
                    aria-label="清除 API 配置"
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ) : (
                <>
                  <Input
                    id="byok-llm"
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="粘贴你的 API Key"
                    value={draftKey}
                    onChange={(event) => {
                      setDraftKey(event.target.value)
                      setError(null)
                    }}
                    data-testid="byok-llm-input"
                    aria-invalid={error ? true : undefined}
                    aria-describedby={error ? 'byok-llm-error' : undefined}
                  />
                  {config.key ? (
                    <Button type="button" variant="ghost" size="sm" onClick={() => setEditingKey(false)}>
                      取消更换
                    </Button>
                  ) : null}
                </>
              )}
              {error ? (
                <p className="text-xs text-destructive" data-testid="byok-llm-error" role="alert">
                  {error}
                </p>
              ) : null}
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                <a
                  href={keyUrl}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                >
                  {keyUrlName} 取 Key
                  <ExternalLink className="size-3" aria-hidden />
                </a>
                {activePreset?.keyHint ? (
                  <span className="text-xs text-muted-foreground">{activePreset.keyHint}</span>
                ) : null}
              </div>
            </div>
          </div>
        </div>

        <div className="flex gap-2 rounded-lg bg-muted/50 p-3 text-xs text-muted-foreground">
          <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden />
          <p data-testid="byok-storage-note">
            填写后只保存在这台设备的浏览器里，不用每次重填。生成总结时，Key 会随请求发给本站后端，后端用它调用你选的
            AI 服务商，用完即丢弃：不写入任务记录、不写进日志、不随任务存下来。公用电脑上用完建议清除。
          </p>
        </div>

        <DialogFooter className="items-center justify-between gap-2 sm:justify-between">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleClear}
            data-testid="byok-clear-all"
            disabled={!hasAnyConfig}
          >
            清除
          </Button>
          <div className="flex gap-2">
            <Button type="button" onClick={handleSave} data-testid="byok-llm-save">
              保存
            </Button>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} data-testid="byok-done">
              完成
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
