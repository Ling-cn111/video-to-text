'use client'

import { useState } from 'react'
import { KeyRound } from 'lucide-react'

import { ApiSettingsDialog } from '@/components/layout/api-settings-dialog'
import { Button } from '@/components/ui/button'

/**
 * 顶栏的「API 设置」入口。
 *
 * 为什么放在顶栏而不是首页表单里：它是**全局偏好**（对结果页的总结、进度页的云端
 * 转写都生效），跟着页面走会让人以为只对当前这次转写有用。放在顶栏与主题切换并列，
 * 语义上就是「这台设备的设置」。
 */
export function ApiSettingsButton() {
  const [open, setOpen] = useState(false)

  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="gap-1.5 px-2"
        onClick={() => setOpen(true)}
        data-testid="open-api-settings"
        title="API 设置：填自己的 Key，用各自的额度"
      >
        <KeyRound className="size-4" aria-hidden />
        <span className="hidden sm:inline">API 设置</span>
      </Button>
      <ApiSettingsDialog open={open} onOpenChange={setOpen} />
    </>
  )
}
