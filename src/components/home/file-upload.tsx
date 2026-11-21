'use client'

import { useRef, useState } from 'react'
import { toast } from 'sonner'
import { useRouter } from 'next/navigation'
import { FileVideo, Loader2, UploadCloud, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useTaskStore } from '@/stores/task-store'
import { processingHref } from '@/lib/routes'
import { FILE_ALLOWED_EXTENSIONS, FILE_MAX_DURATION_SECONDS, FILE_MAX_SIZE_MB, probeVideoDuration, validateVideoFile } from '@/lib/file-upload'

const ACCEPT_ATTR = FILE_ALLOWED_EXTENSIONS.map((ext) => `.${ext}`).join(',') + ',video/mp4,video/quicktime,video/webm'

/**
 * 首页「上传本地视频」入口（M3）。
 *
 * 行为锁点（详见 docs/frontend-features.md §21）：
 * - 入口渲染由 capabilities.fileUploadConfigured 驱动（OSS 未配置时整块不渲染，
 *   与 TranscribeMode 依赖 localAsrAvailable 同型），绝不写死
 * - 预校验在前端完成（扩展名 / 500MB / 约 50 分钟时长探测），失败不发任何请求；
 *   错误码与后端共用（FILE_TOO_LARGE / FILE_FORMAT_UNSUPPORTED）
 * - 上传进度是浏览器直传 OSS 的真实百分比（XHR upload.onprogress），可取消
 */
export function FileUpload() {
  const router = useRouter()
  const submitFile = useTaskStore((s) => s.submitFile)
  const capabilities = useTaskStore((s) => s.capabilities)
  const inputRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const [busy, setBusy] = useState(false)
  const [percent, setPercent] = useState(0)
  const [dragOver, setDragOver] = useState(false)

  // OSS 未配置（或探测未完成）时不渲染：与「未配置的对应功能明确提示」一致——
  // 这里选择整块隐藏，用户连触发错误的机会都没有
  if (capabilities?.fileUploadConfigured !== true) return null

  const handleFile = async (file: File | undefined) => {
    if (!file || busy) return
    // 预校验第一层：扩展名 + 大小（错误码与后端一致，直接复用 toast 链路）
    const validation = validateVideoFile(file)
    if (validation) {
      toast.error(validation.message)
      return
    }
    // 预校验第二层：时长探测（浏览器免费拿元数据；探测失败不拦截，交给后端裁决）
    const duration = await probeVideoDuration(file)
    if (duration !== null && duration > FILE_MAX_DURATION_SECONDS) {
      toast.error(
        `视频时长约 ${Math.round(duration / 60)} 分钟，超过当前约 ${Math.round(FILE_MAX_DURATION_SECONDS / 60)} 分钟的处理上限，请裁剪后再上传`,
      )
      return
    }

    setBusy(true)
    setPercent(0)
    const controller = new AbortController()
    abortRef.current = controller
    const taskId = await submitFile(file, setPercent, controller.signal)
    setBusy(false)
    abortRef.current = null

    if (taskId) {
      router.push(processingHref(taskId))
      return
    }
    const error = useTaskStore.getState().error
    // 用户主动取消：store 里的 NETWORK_ERROR 文案换成更准确的「已取消」
    if (controller.signal.aborted) {
      toast.info('已取消上传')
      return
    }
    toast.error(error?.message ?? '上传失败，请稍后重试')
  }

  const handleCancel = () => {
    abortRef.current?.abort()
  }

  return (
    <div
      data-testid="file-upload"
      className={`relative rounded-xl border border-dashed transition-colors ${
        dragOver ? 'border-primary bg-primary/5' : 'border-border'
      } ${busy ? 'pointer-events-none opacity-90' : 'cursor-pointer hover:border-primary/60 hover:bg-muted/30'}`}
      role="button"
      tabIndex={0}
      // aria-label 刻意避开「本地」二字：锁定 4 用 getByRole('button', { name: '本地' })
      // （子串匹配）定位转写模式按钮，这里的可访问名若含「本地」会造成 strict mode 冲突
      aria-label="选择视频文件上传"
      onClick={() => !busy && inputRef.current?.click()}
      onKeyDown={(event) => {
        if (!busy && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault()
          inputRef.current?.click()
        }
      }}
      onDragOver={(event) => {
        event.preventDefault()
        if (!busy) setDragOver(true)
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(event) => {
        event.preventDefault()
        setDragOver(false)
        void handleFile(event.dataTransfer.files?.[0])
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT_ATTR}
        className="sr-only"
        aria-hidden
        tabIndex={-1}
        data-testid="file-upload-input"
        onChange={(event) => {
          void handleFile(event.target.files?.[0])
          event.target.value = '' // 允许连续选择同一个文件
        }}
      />
      {busy ? (
        <div className="flex flex-col gap-2 px-4 py-4" data-testid="file-upload-progress">
          <div className="flex items-center gap-2 text-sm">
            <Loader2 className="size-4 animate-spin text-primary" aria-hidden />
            <span>
              正在上传… <span className="tabular-nums font-medium">{percent}%</span>
            </span>
            <span className="flex-1" />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 gap-1 px-2 text-xs text-muted-foreground"
              onClick={(event) => {
                event.stopPropagation()
                handleCancel()
              }}
              data-testid="file-upload-cancel"
            >
              <X className="size-3.5" aria-hidden />
              取消
            </Button>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${percent}%` }} />
          </div>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-1.5 px-4 py-5 text-center">
          <div className="flex items-center gap-2 text-sm font-medium">
            <UploadCloud className="size-4 text-primary" aria-hidden />
            上传本地视频转写
          </div>
          <p className="flex items-center gap-1 text-xs text-muted-foreground">
            <FileVideo className="size-3" aria-hidden />
            拖到此处或点击选择 · {FILE_ALLOWED_EXTENSIONS.join(' / ')} · 不超过 {FILE_MAX_SIZE_MB}MB
          </p>
        </div>
      )}
    </div>
  )
}
