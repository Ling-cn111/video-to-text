'use client'

import { AlertCircle, RotateCcw } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { useRateLimitCooldown } from '@/hooks/use-rate-limit-cooldown'
import { errorTitle } from '@/lib/error-titles'
import type { UiError } from '@/stores/task-store'

interface ErrorAlertProps {
  error: UiError
  onRetry?: () => void
  retrying?: boolean
  onBack: () => void
}

/** 错误提示 + 重试 / 返回首页操作 */
export function ErrorAlert({ error, onRetry, retrying = false, onBack }: ErrorAlertProps) {
  const rateLimited = error.code === 'RATE_LIMITED'
  const cooldown = useRateLimitCooldown(rateLimited, error.retryAfter)
  const retryBlocked = rateLimited && cooldown > 0

  return (
    <Alert variant="destructive" role="alert" className="flex flex-col gap-4 sm:flex-row sm:items-center">
      <AlertCircle className="size-5 shrink-0" aria-hidden />
      <div className="flex-1 space-y-1">
        <AlertTitle>{errorTitle(error.code)}</AlertTitle>
        <AlertDescription>
          {error.message}
          {rateLimited ? (
            <span data-testid="rate-limit-hint" className="mt-1 block text-xs">
              {retryBlocked
                ? `为避免继续触发限制，约 ${cooldown} 秒后可重试`
                : '冷却结束，现在可以重试了'}
            </span>
          ) : null}
        </AlertDescription>
      </div>
      <div className="flex shrink-0 gap-2">
        {onRetry ? (
          <Button
            size="sm"
            variant="outline"
            onClick={onRetry}
            disabled={retrying || retryBlocked}
            className="gap-1.5"
          >
            <RotateCcw className="size-3.5" aria-hidden />
            {retrying ? '重试中…' : '重试'}
          </Button>
        ) : null}
        <Button size="sm" variant="ghost" onClick={onBack}>
          返回首页
        </Button>
      </div>
    </Alert>
  )
}

/**
 * 错误码 → 中文标题的表在 `src/lib/error-titles.ts`（纯函数，E2E 可直接 import 断言完整性）。
 */

