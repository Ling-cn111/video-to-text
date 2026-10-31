'use client'

import { useState } from 'react'
import { KeyRound } from 'lucide-react'

import { ApiSettingsDialog } from '@/components/layout/api-settings-dialog'
import { Button } from '@/components/ui/button'
import { IS_MOBILE_SHELL } from '@/lib/runtime'

/**
 * 顶栏的「API 设置」入口。
 *
 * 为什么放在顶栏而不是首页表单里：它是**全局偏好**（对结果页的总结、进度页的云端
 * 转写都生效），跟着页面走会让人以为只对当前这次转写有用。放在顶栏与主题切换并列，
 * 语义上就是「这台设备的设置」。
 *
 * ## 手机端必须带文字（真机反馈）
 *
 * 网页端用 `hidden sm:inline` 把文字收起来，只留一个钥匙图标 —— 在宽屏顶栏里
 * 那是正常的节省空间。但手机顶栏只有三四个图标的位置，一个没有文字的钥匙图标
 * 混在「主题切换」旁边，用户根本不会意识到那是一个能打开设置面板的入口，
 * 真机反馈的原话是「手机端没看到有设置选项」。
 *
 * 所以移动端壳里文字**始终可见**，并且带上 `aria-label`：图标再好看，找不到就等于没有。
 */
export function ApiSettingsButton() {
  const [open, setOpen] = useState(false)

  return (
    <>
      <Button
        type="button"
        variant={IS_MOBILE_SHELL ? 'outline' : 'ghost'}
        size="sm"
        className="gap-1.5 px-2"
        onClick={() => setOpen(true)}
        data-testid="open-api-settings"
        aria-label="API 设置：填自己的 Key，用各自的额度"
        title="API 设置：填自己的 Key，用各自的额度"
      >
        <KeyRound className="size-4" aria-hidden />
        <span className={IS_MOBILE_SHELL ? 'inline' : 'hidden sm:inline'}>API 设置</span>
      </Button>
      <ApiSettingsDialog open={open} onOpenChange={setOpen} />
    </>
  )
}
