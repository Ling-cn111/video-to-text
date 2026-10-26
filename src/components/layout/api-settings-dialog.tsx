'use client'

import { useEffect, useState } from 'react'
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
import { notifyByokChanged } from '@/hooks/use-byok-status'
import {
  ASR_KEY_PROVIDERS,
  LLM_KEY_PROVIDERS,
  clearAllKeys,
  maskKey,
  readAsrKey,
  readLlmKey,
  saveAsrKey,
  saveLlmKey,
  validateKeyFormat,
  type KeyProvider,
} from '@/lib/byok'

/**
 * API 设置面板：让用户填自己的 Key，用各自的额度。
 *
 * 三条界面约束：
 * 1. **默认遮住已填的 Key**（只露末 4 位）。设置面板经常在投屏、录屏、截图的场景
 *    下打开，明文铺在屏幕上等于泄露。
 * 2. **保存前不做「有效性」判断**，只拦手误（空格/超长/过短）。Key 是否有效只有
 *    服务商知道；在这里假装能验证，用户会以为填对了，直到转写失败才发现。
 * 3. **必须写清存储位置与风险**。用户把 Key 交给一个网页，有权知道它存在哪、
 *    会不会上传。含糊其辞比不写更糟。
 */

interface KeyFieldProps {
  id: string
  title: string
  description: string
  providers: KeyProvider[]
  /** 当前已保存的值（挂载后从 localStorage 读，避免 hydration mismatch） */
  stored: string | null
  onSave: (value: string | null) => void
  testId: string
}

function KeyField({ id, title, description, providers, stored, onSave, testId }: KeyFieldProps) {
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
    toast.success(`${title} 已清除，将改用站点提供的额度`)
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
            使用站点额度
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
  const [asrKey, setAsrKey] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setLlmKey(readLlmKey())
    setAsrKey(readAsrKey())
  }, [open])

  function handleSaveLlm(value: string | null) {
    saveLlmKey(value)
    setLlmKey(value)
    // 通知同页的能力门禁重算：刚填了 Key，结果页那个灰掉的「生成总结」应当立刻可用
    notifyByokChanged()
  }

  function handleSaveAsr(value: string | null) {
    saveAsrKey(value)
    setAsrKey(value)
    notifyByokChanged()
  }

  function handleClearAll() {
    clearAllKeys()
    setLlmKey(null)
    setAsrKey(null)
    notifyByokChanged()
    toast.success('已清除全部 Key，将改用站点提供的额度')
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
            填自己的 Key 就用各自的额度，不占用站点配额。不填则使用站点提供的额度（可能因共享而排队）。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <KeyField
            id="byok-llm"
            title="AI 总结 Key"
            description="用于生成概要、要点与章节笔记"
            providers={LLM_KEY_PROVIDERS}
            stored={llmKey}
            onSave={handleSaveLlm}
            testId="byok-llm"
          />
          <KeyField
            id="byok-asr"
            title="语音识别 Key"
            description="用于云端转写（本地模式不需要 Key）"
            providers={ASR_KEY_PROVIDERS}
            stored={asrKey}
            onSave={handleSaveAsr}
            testId="byok-asr"
          />
        </div>

        <div className="flex gap-2 rounded-lg bg-muted/50 p-3 text-xs text-muted-foreground">
          <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden />
          <p>
            Key 只保存在这台设备的浏览器里，不会上传到服务器，也不会写进日志。请求时通过请求头直接发给你选的服务商。
            公用电脑上用完建议清除。
          </p>
        </div>

        <DialogFooter className="items-center justify-between gap-2 sm:justify-between">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleClearAll}
            data-testid="byok-clear-all"
            disabled={!llmKey && !asrKey}
          >
            清除全部
          </Button>
          <Button type="button" onClick={() => onOpenChange(false)} data-testid="byok-done">
            完成
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
