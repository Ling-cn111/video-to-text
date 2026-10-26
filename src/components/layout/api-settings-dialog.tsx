'use client'

import { useCallback, useEffect, useState } from 'react'
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
import { LLM_KEY_PROVIDERS, maskKey, readLlmKey, saveLlmKey, validateKeyFormat } from '@/lib/byok'

/**
 * 一个 Key 字段。三条界面约束，改之前先想清楚：
 *
 * 1. **默认遮住**：填完立刻变掩码。投屏、录屏、截图、旁边有人看一眼，都不该泄露。
 * 2. **保存前不做有效性判断**：只有服务商知道 Key 能不能用，本地瞎猜会把合法 Key 拒掉。
 *    真正的校验发生在第一次调用，错误码 `BYOK_KEY_INVALID` 会把用户指回这个面板。
 * 3. **必须写清存储位置与边界**：见面板底部说明，不能含糊成「不会上传」。
 */
function KeyField({
  id,
  title,
  description,
  providers,
  stored,
  onSave,
  testId,
}: {
  id: string
  title: string
  description: string
  providers: typeof LLM_KEY_PROVIDERS
  stored: string | null
  onSave: (value: string | null) => void
  testId: string
}) {
  const [draft, setDraft] = useState('')
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 已保存且没在编辑 → 显示掩码；点「更换」才露出输入框
  const showingStored = Boolean(stored) && !editing

  function handleSave() {
    const result = validateKeyFormat(draft)
    if (!result.ok) {
      setError(result.reason)
      return
    }
    setError(null)
    onSave(result.value)
    setDraft('')
    setEditing(false)
    toast.success(`${title} 已保存，只存在这台设备的浏览器里`)
  }

  function handleClear() {
    onSave(null)
    setDraft('')
    setEditing(false)
    setError(null)
    toast.success(`${title} 已清除`)
  }

  return (
    <div className="space-y-2 rounded-lg border p-4" data-testid={testId}>
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-0.5">
          <Label htmlFor={id} className="text-sm font-medium">
            {title}
          </Label>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
        {stored ? (
          <span className="shrink-0 rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-600 dark:text-emerald-400">
            已启用
          </span>
        ) : (
          <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
            未填写
          </span>
        )}
      </div>

      {showingStored ? (
        <div className="flex items-center gap-2">
          <code
            className="flex-1 truncate rounded-md border bg-muted/40 px-3 py-1.5 font-mono text-sm"
            data-testid={`${testId}-masked`}
          >
            {maskKey(stored as string)}
          </code>
          <Button type="button" variant="outline" size="sm" onClick={() => setEditing(true)}>
            更换
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleClear}
            data-testid={`${testId}-clear`}
            aria-label={`清除 ${title}`}
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          <Input
            id={id}
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="粘贴你的 API Key"
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value)
              setError(null)
            }}
            data-testid={`${testId}-input`}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${testId}-error` : undefined}
          />
          {error ? (
            <p className="text-xs text-destructive" data-testid={`${testId}-error`} role="alert">
              {error}
            </p>
          ) : null}
          <div className="flex items-center gap-2">
            <Button type="button" size="sm" onClick={handleSave} data-testid={`${testId}-save`}>
              保存
            </Button>
            {stored ? (
              <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(false)}>
                取消
              </Button>
            ) : null}
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-x-3 gap-y-1 pt-1">
        {providers.map((provider) => (
          <a
            key={provider.id}
            href={provider.url}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            {provider.name} 取 Key
            <ExternalLink className="size-3" aria-hidden />
          </a>
        ))}
      </div>
    </div>
  )
}

export interface ApiSettingsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ApiSettingsDialog({ open, onOpenChange }: ApiSettingsDialogProps) {
  // 初始 null，挂载后再读 localStorage：服务端没有 localStorage，
  // 直接在 useState 初值里读会导致 hydration 前后 DOM 不一致
  const [llmKey, setLlmKey] = useState<string | null>(null)

  // 任意位置（结果页总结卡、首页提示）都能把这个面板叫起来
  const handleExternalOpen = useCallback(() => onOpenChange(true), [onOpenChange])
  useApiSettingsOpenSignal(handleExternalOpen)

  useEffect(() => {
    if (!open) return
    setLlmKey(readLlmKey())
  }, [open])

  function handleSaveLlm(value: string | null) {
    saveLlmKey(value)
    setLlmKey(value)
    // 通知同页的能力门禁重算：刚填了 Key，结果页那个灰掉的「生成总结」应当立刻可用
    notifyByokChanged()
  }

  function handleClearAll() {
    saveLlmKey(null)
    setLlmKey(null)
    notifyByokChanged()
    toast.success('已清除 API Key')
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl" data-testid="api-settings-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyRound className="size-4" aria-hidden />
            API 设置
          </DialogTitle>
          <DialogDescription>
            填自己的 AI 总结 Key，就用你自己的额度，不占用站点配额。不填则走站点额度（本站默认不提供，
            需要自己填）。语音识别用的是免费模型，不需要填 Key。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <KeyField
            id="byok-llm"
            title="AI 总结 Key"
            description="用于生成一句话概要、核心要点与章节笔记"
            providers={LLM_KEY_PROVIDERS}
            stored={llmKey}
            onSave={handleSaveLlm}
            testId="byok-llm"
          />
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
            onClick={handleClearAll}
            data-testid="byok-clear-all"
            disabled={!llmKey}
          >
            清除
          </Button>
          <Button type="button" onClick={() => onOpenChange(false)} data-testid="byok-done">
            完成
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
