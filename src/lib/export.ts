import { formatDateTime, formatTimestamp } from '@/lib/format'
import { getPlatformMeta } from '@/lib/platforms/registry'
import type { Summary, TranscriptItem, VideoInfo } from '@/lib/types'

/**
 * 导出工具：把转写结果组织为 TXT / Markdown 并触发下载。
 */

export type ExportFormat = 'txt' | 'md'

export interface ExportInput {
  video: VideoInfo
  transcript: TranscriptItem[]
  summary: Summary
}

function platformName(video: VideoInfo): string {
  return getPlatformMeta(video.platform).name
}

function buildHeaderLines(video: VideoInfo): string[] {
  return [
    `平台：${platformName(video)}`,
    `时长：${formatTimestamp(video.duration)}`,
    `视频 ID：${video.videoId}`,
    `导出时间：${formatDateTime()}`,
  ]
}

export function buildPlainText({ video, transcript, summary }: ExportInput): string {
  const lines: string[] = []
  lines.push(`《${video.title}》`)
  lines.push('')
  lines.push(buildHeaderLines(video).join(' ｜ '))
  lines.push('')

  lines.push('━━━━━━ AI 总结 ━━━━━━')
  lines.push(`一句话概要：${summary.summary}`)
  lines.push('')
  lines.push('核心要点：')
  summary.keyPoints.forEach((point, index) => {
    lines.push(`${index + 1}. ${point}`)
  })
  lines.push('')
  lines.push('章节笔记：')
  summary.chapters.forEach((chapter) => {
    lines.push(`- [${formatTimestamp(chapter.time)}] ${chapter.title}：${chapter.note}`)
  })
  lines.push('')

  lines.push('━━━━━━ 文字稿 ━━━━━━')
  transcript.forEach((item) => {
    lines.push(`[${formatTimestamp(item.time)}] ${item.text}`)
  })

  return lines.join('\n')
}

export function buildMarkdown({ video, transcript, summary }: ExportInput): string {
  const lines: string[] = []
  lines.push(`# 《${video.title}》`)
  lines.push('')
  lines.push(`> ${buildHeaderLines(video).join(' ｜ ')}`)
  lines.push('')
  lines.push('## AI 总结')
  lines.push('')
  lines.push(`**一句话概要**：${summary.summary}`)
  lines.push('')
  lines.push('### 核心要点')
  lines.push('')
  summary.keyPoints.forEach((point, index) => {
    lines.push(`${index + 1}. ${point}`)
  })
  lines.push('')
  lines.push('### 章节笔记')
  lines.push('')
  summary.chapters.forEach((chapter) => {
    lines.push(`- **[${formatTimestamp(chapter.time)}] ${chapter.title}**：${chapter.note}`)
  })
  lines.push('')
  lines.push('## 文字稿')
  lines.push('')
  transcript.forEach((item) => {
    lines.push(`- \`${formatTimestamp(item.time)}\` ${item.text}`)
  })

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

export function downloadTextFile(filename: string, content: string): void {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
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
