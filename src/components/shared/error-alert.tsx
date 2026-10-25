'use client'

import { AlertCircle, RotateCcw } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import type { ClientErrorCode } from '@/lib/api'
import type { UiError } from '@/stores/task-store'

interface ErrorAlertProps {
  error: UiError
  onRetry?: () => void
  retrying?: boolean
  onBack: () => void
}

/** 错误提示 + 重试 / 返回首页操作 */
export function ErrorAlert({ error, onRetry, retrying = false, onBack }: ErrorAlertProps) {
  return (
    <Alert variant="destructive" role="alert" className="flex flex-col gap-4 sm:flex-row sm:items-center">
      <AlertCircle className="size-5 shrink-0" aria-hidden />
      <div className="flex-1 space-y-1">
        <AlertTitle>{errorTitle(error.code)}</AlertTitle>
        <AlertDescription>{error.message}</AlertDescription>
      </div>
      <div className="flex shrink-0 gap-2">
        {onRetry ? (
          <Button size="sm" variant="outline" onClick={onRetry} disabled={retrying} className="gap-1.5">
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
 * 错误码 → 中文标题。
 *
 * 用 `Record<ClientErrorCode, string>` 而不是 switch：新增错误码时 TypeScript 会直接
 * 报「缺少属性」，逼着这里同步，不会再出现「后端返回了新码、前端只显示『出了点问题』」。
 * 码表来源：`src/lib/types.ts` 的 ApiErrorCode（后端 `backend/app/errors.py` 为准）
 * + `src/lib/http.ts` 的客户端网络层码。
 */
const ERROR_TITLES: Record<ClientErrorCode, string> = {
  // 解析 / 链接
  INVALID_URL: '链接无效',
  UNSUPPORTED_PLATFORM: '平台暂不支持',
  // 任务
  INVALID_TASK: '任务不存在',
  TASK_INTERRUPTED: '任务已中断',
  TASK_TIMEOUT: '处理超时',
  // 转写
  TRANSCRIBE_FAILED: '转写失败',
  AUDIO_TOO_LARGE: '音频过大',
  // 总结
  CLOUD_NOT_CONFIGURED: '未配置云端 ASR',
  SUMMARIZE_NOT_CONFIGURED: '未配置 AI 总结',
  TRANSCRIPT_TOO_LONG: '文字稿过长',
  SUMMARIZE_FAILED: '总结失败',
  // 兜底
  INTERNAL_ERROR: '服务异常',
  NETWORK_ERROR: '网络异常',
  UNKNOWN_ERROR: '出了点问题',
}

function errorTitle(code: ClientErrorCode): string {
  return ERROR_TITLES[code] ?? '出了点问题'
}
