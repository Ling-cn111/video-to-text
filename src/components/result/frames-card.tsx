'use client'

/* eslint-disable @next/next/no-img-element */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ImageOff, Images, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { api, ApiError } from '@/lib/api'
import { formatTimestamp } from '@/lib/format'
import { deriveFrameTimestamps } from '@/lib/frames'
import { useTaskStore } from '@/stores/task-store'

/** 抽帧轮询间隔：真实后端 12 帧约需 1-3 分钟（下载视频流 + 逐点抽取） */
const FRAMES_POLL_INTERVAL_MS = 2500

/**
 * 重点帧卡（PR #89）：总结就绪后可按章节/要点时间点生成视频画面截图。
 *
 * 状态机（store.frames）：
 * - null：未请求 → 「生成重点帧」按钮（手动触发，成本可控——抽帧要下载视频流）
 * - processing：抽帧进行中 → 骨架屏 + 本地轮询任务详情（2.5s）
 * - ready：网格展示（<img> 直连预签名 GET，限时凭证过期后重拉详情即续期）
 * - failed：错误信息 + 重试（后端幂等语义：failed 允许重新派发）
 *
 * 渲染条件：capabilities.framesConfigured（OSS 配置探测）+ 非 local 平台
 * （文件任务源对象成功即删，无源可抽）+ AI 总结就绪（时间戳来自总结）。
 * 可访问名刻意避开「本地」二字（锁定项 4 的子串选择器 strict-mode 冲突）。
 */
export function FramesCard() {
  const phase = useTaskStore((state) => state.phase)
  const taskId = useTaskStore((state) => state.taskId)
  const video = useTaskStore((state) => state.video)
  const summary = useTaskStore((state) => state.summary)
  const frames = useTaskStore((state) => state.frames)
  const capabilities = useTaskStore((state) => state.capabilities)
  const setFrames = useTaskStore((state) => state.setFrames)
  const [requesting, setRequesting] = useState(false)
  const [requestError, setRequestError] = useState<string | null>(null)
  const pollInFlight = useRef(false)

  const enabled = capabilities?.framesConfigured === true
  const visible = phase === 'done' && enabled && summary !== null && video !== null && video.platform !== 'local'

  const stopPolling = useCallback(() => {
    pollInFlight.current = false
  }, [])

  // processing → 轮询任务详情直到 frames 终态（组件卸载 / 状态离开 processing 即停）
  useEffect(() => {
    if (frames?.status !== 'processing' || !taskId) return
    let cancelled = false
    pollInFlight.current = false
    const timer = setInterval(() => {
      if (pollInFlight.current) return
      pollInFlight.current = true
      api
        .getTask(taskId)
        .then((detail) => {
          if (cancelled) return
          // 详情没带 frames（旧后端 / Mock）时保持 processing，下一轮再试
          if (detail.frames) setFrames(detail.frames)
        })
        .catch(() => {
          // 单次轮询失败静默重试：抽帧是分钟级作业，一次网络抖动不值得打断
        })
        .finally(() => {
          pollInFlight.current = false
        })
    }, FRAMES_POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      stopPolling()
      clearInterval(timer)
    }
  }, [frames?.status, taskId, setFrames, stopPolling])

  const handleGenerate = useCallback(async () => {
    if (!taskId || !summary || requesting) return
    setRequesting(true)
    setRequestError(null)
    try {
      const timestamps = deriveFrameTimestamps(summary, video?.duration ?? 0)
      const result = await api.requestFrames({ taskId, timestamps })
      setFrames(result.frames)
    } catch (error) {
      // 404 / 409 / 422 等校验类失败：不打进 frames 状态机（那是异步作业的状态），
      // 直接展示请求错误；重试按钮仍可用
      setRequestError(error instanceof ApiError ? error.message : '重点帧请求失败，请稍后重试')
    } finally {
      setRequesting(false)
    }
  }, [requesting, setFrames, summary, taskId, video?.duration])

  if (!visible) return null

  return (
    <Card data-testid="frames-card">
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Images className="size-4 text-primary" aria-hidden />
          重点帧
        </div>
        <span className="text-xs text-muted-foreground">按 AI 总结的章节与要点定位画面</span>
      </CardHeader>
      <CardContent>
        {frames?.status === 'ready' && frames.items.length > 0 ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3" data-testid="frames-grid">
            {frames.items.map((item) => (
              <figure key={item.objectKey} className="flex flex-col gap-1.5" data-testid="frames-item">
                <div className="relative overflow-hidden rounded-md border bg-muted">
                  {item.url ? (
                    <img
                      src={item.url}
                      alt={`第 ${formatTimestamp(item.time)} 的画面`}
                      loading="lazy"
                      referrerPolicy="no-referrer"
                      className="aspect-video size-full object-cover"
                    />
                  ) : (
                    /* 预签名失败 / URL 缺失：占位不破格 */
                    <div
                      className="flex aspect-video size-full items-center justify-center"
                      data-testid="frames-item-fallback"
                    >
                      <ImageOff className="size-6 text-muted-foreground" aria-hidden />
                    </div>
                  )}
                </div>
                <figcaption className="text-xs tabular-nums text-muted-foreground" data-testid="frames-time">
                  {formatTimestamp(item.time)}
                </figcaption>
              </figure>
            ))}
          </div>
        ) : frames?.status === 'processing' ? (
          <div className="flex flex-col gap-3" data-testid="frames-processing">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              {[0, 1, 2, 3, 4, 5].map((index) => (
                <Skeleton key={index} className="aspect-video w-full" />
              ))}
            </div>
            <p className="text-xs text-muted-foreground">正在从视频里抽取重点画面，通常需要 1-3 分钟…</p>
          </div>
        ) : frames?.status === 'failed' ? (
          <div className="flex flex-col gap-3" data-testid="frames-failed">
            <p className="text-sm text-destructive">{frames.error?.message ?? '重点帧生成失败，请稍后重试'}</p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleGenerate()}
              disabled={requesting}
              className="gap-1.5 self-start"
              data-testid="frames-retry"
            >
              <RefreshCw className="size-3.5" aria-hidden />
              {requesting ? '请求中…' : '重试'}
            </Button>
          </div>
        ) : (
          <div className="flex flex-col gap-3" data-testid="frames-idle">
            <p className="text-xs text-muted-foreground">
              根据总结定位 {deriveFrameTimestamps(summary, video?.duration ?? 0).length} 个关键画面，点击生成后可查看对应时刻的视频截图。
            </p>
            {requestError ? <p className="text-sm text-destructive">{requestError}</p> : null}
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleGenerate()}
              disabled={requesting}
              className="gap-1.5 self-start"
              data-testid="frames-generate"
            >
              <Images className="size-3.5" aria-hidden />
              {requesting ? '请求中…' : '生成重点帧'}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
