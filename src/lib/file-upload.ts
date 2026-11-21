import { ApiError } from '@/lib/http'

/**
 * 本地文件上传的**客户端预校验**（M3）。
 *
 * 为什么要三道本地校验：后端也有同样的校验（upload/init 声明 + HEAD 实际大小 +
 * Worker 流式），但等后端拒绝之前用户已经白等了一次上传——尤其是时长，只有
 * 浏览器能免费拿到（HTMLVideoElement 读本地文件元数据），上传 300MB 之后才
 * 发现「视频太长」是最差的体验。前后端共用同一组错误码（FILE_TOO_LARGE /
 * FILE_FORMAT_UNSUPPORTED），toast 文案口径一致。
 *
 * 数值必须与后端默认值对齐：backend/app/config.py 的 FILE_MAX_SIZE_MB=500、
 * FILE_ALLOWED_EXTENSIONS=mp4,mov,webm；时长上限对应后端转码后的 WAV 上限
 * （MAX_AUDIO_SIZE_MB=100 ÷ 32000 字节/秒 ≈ 54.6 分钟），取 50 分钟留余量。
 */

export const FILE_ALLOWED_EXTENSIONS = ['mp4', 'mov', 'webm'] as const

export const FILE_MAX_SIZE_MB = 500

/** 软性时长上限（秒）：约 50 分钟。探测失败时不拦截，交给后端裁决 */
export const FILE_MAX_DURATION_SECONDS = 50 * 60

export function fileExtension(fileName: string): string {
  const name = fileName.replace(/\\/g, '/')
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : ''
}

/**
 * 扩展名 + 大小校验。返回 null 表示通过，否则返回**带契约错误码的 ApiError**，
 * 调用方直接交给 toast/错误提示链路（errorTitle 按 code 出中文标题）。
 */
export function validateVideoFile(file: File): ApiError | null {
  const extension = fileExtension(file.name)
  if (!FILE_ALLOWED_EXTENSIONS.includes(extension as (typeof FILE_ALLOWED_EXTENSIONS)[number])) {
    return new ApiError(
      'FILE_FORMAT_UNSUPPORTED',
      `暂不支持「${extension || '无扩展名'}」格式，请上传 mp4 / mov / webm 视频`,
      0,
    )
  }
  if (file.size <= 0) {
    return new ApiError('FILE_TOO_LARGE', '文件大小异常，请重新选择文件', 0)
  }
  if (file.size > FILE_MAX_SIZE_MB * 1024 * 1024) {
    return new ApiError('FILE_TOO_LARGE', `文件超过 ${FILE_MAX_SIZE_MB}MB 上限，请压缩或裁剪后再上传`, 0)
  }
  return null
}

/**
 * 本地探测视频时长（秒）。HEVC/mov 等浏览器解不了的格式会失败 → 返回 null
 * （不拦截，放行上传，让后端在转码阶段裁决）。
 *
 * 只读元数据（loadedmetadata），不真正解码视频内容，500MB 文件也是瞬时的。
 */
export function probeVideoDuration(file: File, timeoutMs = 8000): Promise<number | null> {
  return new Promise((resolve) => {
    if (typeof window === 'undefined' || typeof URL?.createObjectURL !== 'function') {
      resolve(null)
      return
    }
    const url = URL.createObjectURL(file)
    const video = document.createElement('video')
    video.preload = 'metadata'
    const cleanup = (value: number | null) => {
      window.clearTimeout(timer)
      URL.revokeObjectURL(url)
      video.src = ''
      resolve(value)
    }
    const timer = window.setTimeout(() => cleanup(null), timeoutMs)
    video.onloadedmetadata = () => {
      cleanup(Number.isFinite(video.duration) && video.duration > 0 ? video.duration : null)
    }
    video.onerror = () => cleanup(null)
    video.src = url
  })
}
