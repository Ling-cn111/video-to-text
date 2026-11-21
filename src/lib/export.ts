import { formatDurationWords } from '@/lib/format'
import { buildMindmapMarkdown, type MindmapNode } from '@/lib/mindmap'
import { getPlatformMeta } from '@/lib/platforms/registry'
import { shareTextFile, type ShareOutcome } from '@/lib/share-file'
import { buildSrt, buildVtt } from '@/lib/subtitle-export'
import type { TranscriptItem, VideoInfo } from '@/lib/types'

/**
 * 导出工具：导出「全文阅读」式的纯文本文章（不含任何时间戳）。
 * 段落由 formatTranscriptToArticle 从文字稿派生（见 src/lib/article.ts）。
 *
 * 三类导出，绝不混用（锁定项 §2，PR18 起新增第三组）：
 * - `txt` / `md`：阅读产物，**全文件零时间戳**，受铁律约束
 * - `srt` / `vtt`：字幕产物，时间轴就是它的用途，必须带时间戳
 * - 思维导图大纲（`exportMindmapMarkdown`）：第三类产物，独立 builder
 *   （buildMindmapMarkdown），不得复用/污染前两类
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

/**
 * 把内容交给系统：移动端壳内走原生分享，网页 / 桌面端走分享或下载。
 *
 * 历史实现是裸的 `blob:` + `<a download>`，在 Android WebView 里会变成新窗口
 * 或静默失败；改造后统一走 `shareTextFile` 的分级降级（见 src/lib/share-file.ts）：
 * 桌面壳内弹系统「另存为」对话框，移动端壳内走原生分享，网页端走分享或下载。
 *
 * 返回值语义：`cancelled` 是用户主动关掉分享面板，**不是错误**，调用方不要报错。
 */
export async function exportResult(format: ExportFormat, input: ExportInput): Promise<ShareOutcome> {
  const content = format === 'md' ? buildMarkdown(input) : buildPlainText(input)
  return shareTextFile({
    filename: buildExportFilename(format, input.video),
    content,
    mime: 'text/plain;charset=utf-8',
    title: input.video.title,
  })
}

/**
 * 导出字幕（带时间戳）。
 *
 * 与 `exportResult` 分开是刻意的：两者产物语义相反（零时间戳 vs 必须有时间戳），
 * 合并成一个函数后，后来的人很容易顺手在字幕分支里复用 buildPlainText。
 */
export async function exportSubtitles(format: SubtitleFormat, input: SubtitleExportInput): Promise<ShareOutcome> {
  const content =
    format === 'vtt' ? buildVtt(input.transcript, input.video) : buildSrt(input.transcript, input.video.duration)
  return shareTextFile({
    filename: buildSubtitleFilename(format, input.video),
    content,
    // VTT 用 text/vtt：部分播放器靠 MIME 判断能否直接挂载
    mime: format === 'vtt' ? 'text/vtt;charset=utf-8' : 'application/x-subrip;charset=utf-8',
    title: `${input.video.title}-字幕`,
  })
}

/** 思维导图文件名带「思维导图」二字：与文字稿 / 字幕在下载列表里一眼分开 */
export function buildMindmapFilename(video: VideoInfo): string {
  return `${sanitizeFilename(video.title)}-思维导图.md`
}

/**
 * 导出思维导图大纲（PR18）：层级 markdown（不含时间戳），可直接贴入
 * markmap / XMind 等工具。
 *
 * 与 `exportSubtitles` 同理刻意独立：产物语义与「阅读文本」「字幕」都不同，
 * 数据源是 mindmap.ts 推导的树（章节 + 要点 + note 拆句），不碰 buildPlainText /
 * buildMarkdown。
 */
export async function exportMindmapMarkdown(tree: MindmapNode, video: VideoInfo): Promise<ShareOutcome> {
  return shareTextFile({
    filename: buildMindmapFilename(video),
    content: buildMindmapMarkdown(tree),
    mime: 'text/plain;charset=utf-8',
    title: video.title,
  })
}
