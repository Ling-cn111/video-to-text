/* eslint-disable @next/next/no-img-element */
import { getPlatformMeta } from '@/lib/platforms/registry'
import { formatDuration } from '@/lib/format'
import { PlatformBadge } from '@/components/shared/platform-badge'
import { Video } from 'lucide-react'
import type { VideoInfo } from '@/lib/types'

/** 视频信息卡：封面（本地占位图）+ 时长 + 平台徽章 + 标题 */
export function VideoCard({ video }: { video: VideoInfo }) {
  const meta = getPlatformMeta(video.platform)
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
      <div
        className="relative aspect-video w-full shrink-0 overflow-hidden rounded-xl sm:aspect-auto sm:h-24 sm:w-44"
        style={{ background: `linear-gradient(135deg, ${meta.gradient[0]}, ${meta.gradient[1]})` }}
      >
        {video.cover ? (
          <img
            src={video.cover}
            alt=""
            className="size-full object-cover"
            loading="lazy"
            referrerPolicy="no-referrer"
          />
        ) : (
          // 本地文件任务没有封面（M3）：平台色渐变上放一个视频图标占位
          <div className="flex size-full items-center justify-center" data-testid="video-cover-fallback">
            <Video className="size-8 text-white/85" aria-hidden />
          </div>
        )}
        <span className="absolute bottom-1.5 right-1.5 rounded-md bg-black/65 px-1.5 py-0.5 text-[11px] font-medium text-white tabular-nums">
          {formatDuration(video.duration)}
        </span>
      </div>
      <div className="flex min-w-0 flex-col gap-2">
        <PlatformBadge platform={video.platform} className="w-fit" />
        <h2 className="line-clamp-2 text-base font-semibold leading-snug sm:text-lg">{video.title}</h2>
        {/* 本地文件的 videoId 是 OSS 对象键，对用户没有意义且很长，不展示 */}
        {video.platform !== 'local' && (
          <p className="text-xs text-muted-foreground">
            videoId: <span className="font-mono">{video.videoId}</span>
          </p>
        )}
      </div>
    </div>
  )
}
