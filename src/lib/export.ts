import { formatDurationWords } from '@/lib/format'
import { getPlatformMeta } from '@/lib/platforms/registry'
import { buildSrt, buildVtt } from '@/lib/subtitle-export'
import type { TranscriptItem, VideoInfo } from '@/lib/types'

/**
 * 导出工具：导出「全文阅读」式的纯文本文章（不含任何时间戳）。
 * 段落由 formatTranscriptToArticle 从文字稿派生（见 src/lib/article.ts）。
 *
 * 两类导出，绝不混用（锁定项 §2）：
 * - `txt` / `md`：阅读产物，**全文件零时间戳**，受铁律约束
 * - `srt` / `vtt`：字幕产物，时间轴就是它的用途，必须带时间戳
 * 新增字幕格式时走 `exportSubtitles`，不要往 buildPlainText / buildMarkdown 里塞时间。
 */

export type ExportFormat = 'txt' | 'md'
/** 字幕格式：与阅读格式分开，避免「零时间戳」铁律被误伤 */
export type SubtitleFormat = 'srt' | 'vtt'

export interface ExportInput {
  video: VideoInfo
  /** 无时间戳的自然段落 */
  paragraphs: string[]
}

export interface SubtitleExportInput {
  video: VideoInfo
  /** 句级文字稿（带秒级时间戳） */
  transcript: TranscriptItem[]
}

function metaLine(video: VideoInfo): string {
  return `来源：${getPlatformMeta(video.platform).name} ｜ 时长：${formatDurationWords(video.duration)}`
}

export function buildPlainText({ video, paragraphs }: ExportInput): string {
  const lines: string[] = []
  lines.push(`《${video.title}》`)
  lines.push('')
  lines.push(metaLine(video))
  lines.push('')
  lines.push(paragraphs.join('\n\n'))
  return lines.join('\n')
}

export function buildMarkdown({ video, paragraphs }: ExportInput): string {
  const lines: string[] = []
  lines.push(`# ${video.title}`)
  lines.push('')
  lines.push(`> ${metaLine(video)}`)
  lines.push('')
  lines.push('## 正文')
  lines.push('')
  lines.push(paragraphs.join('\n\n'))
  return lines.join('\n')
}

/** 文件名安全化：去除路径分隔符与非法字符，限制长度 */
function sanitizeFilename(title: string): string {
  const cleaned = title
    .replace(/[\\/:*?"<>|#%&{}$!'@+`=\s]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  return (cleaned || '文字稿').slice(0, 60)
}

export function buildExportFilename(format: ExportFormat, video: VideoInfo): string {
  const extension = format === 'md' ? 'md' : 'txt'
  return `${sanitizeFilename(video.title)}-文字稿.${extension}`
}

/** 字幕文件名带「字幕」二字：与「文字稿」在下载列表里一眼能分开 */
export function buildSubtitleFilename(format: SubtitleFormat, video: VideoInfo): string {
  return `${sanitizeFilename(video.title)}-字幕.${format}`
}

export function downloadTextFile(filename: string, content: string, mime = 'text/plain;charset=utf-8'): void {
  const blob = new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

export function exportResult(format: ExportFormat, input: ExportInput): void {
  const content = format === 'md' ? buildMarkdown(input) : buildPlainText(input)
  downloadTextFile(buildExportFilename(format, input.video), content)
}

/**
 * 导出字幕（带时间戳）。
 *
 * 与 `exportResult` 分开是刻意的：两者产物语义相反（零时间戳 vs 必须有时间戳），
 * 合并成一个函数后，后来的人很容易顺手在字幕分支里复用 buildPlainText。
 */
export function exportSubtitles(format: SubtitleFormat, input: SubtitleExportInput): void {
  const content =
    format === 'vtt' ? buildVtt(input.transcript, input.video) : buildSrt(input.transcript, input.video.duration)
  // VTT 用 text/vtt：部分播放器靠 MIME 判断能否直接挂载
  downloadTextFile(buildSubtitleFilename(format, input.video), content, format === 'vtt' ? 'text/vtt;charset=utf-8' : 'application/x-subrip;charset=utf-8')
}
