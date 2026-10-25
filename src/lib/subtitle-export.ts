import { formatDuration } from '@/lib/format'
import type { TranscriptItem, VideoInfo } from '@/lib/types'

/**
 * 字幕格式导出（P1-10）：SRT / WebVTT。
 *
 * 与 TXT / MD 的关系（重要）：
 * - TXT / MD 是「阅读」产物，受锁定项 §2「全文件零时间戳」铁律约束
 * - SRT / VTT 是「字幕」产物，时间轴就是它的全部意义，必须带时间戳
 * 两者不是同一类导出，所以 SRT/VTT 走独立的下拉分组，绝不混进 TXT/MD 的生成函数。
 *
 * 为什么单句 = 单条字幕：文字稿本身就是按句切分的（ASR 输出即句级时间戳），
 * 再按字数二次合并只会把字幕切成半句话，反而更难用。句长过长（>SUB_MAX_CHARS）
 * 时才按标点拆分，并把时间区间按字数比例分配 —— 这是唯一需要算时间的地方。
 */

/** 单条字幕的最大字数：超过就按标点拆成多条（长句硬塞进一行会被播放器截断） */
const SUB_MAX_CHARS = 40

/** 秒 → SRT 时间码 `HH:MM:SS,mmm`（逗号分隔，SRT 规范） */
export function toSrtTime(seconds: number): string {
  const ms = Math.max(0, Math.round(seconds * 1000))
  const hours = Math.floor(ms / 3_600_000)
  const minutes = Math.floor((ms % 3_600_000) / 60_000)
  const secs = Math.floor((ms % 60_000) / 1000)
  const millis = ms % 1000
  const pad = (value: number, width = 2) => String(value).padStart(width, '0')
  return `${pad(hours)}:${pad(minutes)}:${pad(secs)},${pad(millis, 3)}`
}

/** 秒 → WebVTT 时间码 `HH:MM:SS.mmm`（点号分隔，VTT 规范） */
export function toVttTime(seconds: number): string {
  return toSrtTime(seconds).replace(',', '.')
}

interface Cue {
  start: number
  end: number
  text: string
}

/**
 * 句级文字稿 → 字幕条。
 *
 * 结束时间取「下一条的起点」，最后一条用视频总时长兜底。
 * 为什么不用固定时长：中文语速差异很大，固定 3 秒会让长句被截断、短句留白过多。
 */
export function buildCues(transcript: TranscriptItem[], duration: number): Cue[] {
  const cues: Cue[] = []
  const total = duration > 0 ? duration : (transcript.at(-1)?.time ?? 0) + 5

  transcript.forEach((item, index) => {
    const nextTime = transcript[index + 1]?.time ?? total
    // 兜底：时间戳乱序或重复时，给一个最小可读时长，避免出现 end <= start 的非法字幕
    const end = Math.max(nextTime, item.time + 1)
    const text = item.text.trim()
    if (!text) return

    if (text.length <= SUB_MAX_CHARS) {
      cues.push({ start: item.time, end, text })
      return
    }

    // 超长句：按标点拆开，时间区间按字数比例分配，保证字幕不重叠、不倒退
    const pieces = splitByPunctuation(text, SUB_MAX_CHARS)
    const totalChars = pieces.reduce((sum, piece) => sum + piece.length, 0) || 1
    let cursor = item.time
    pieces.forEach((piece) => {
      const share = ((end - item.time) * piece.length) / totalChars
      const pieceEnd = cursor + share
      cues.push({ start: cursor, end: pieceEnd, text: piece })
      cursor = pieceEnd
    })
  })

  return cues
}

/** 按标点切分超长文本；切不动时硬切，保证每条都不超长 */
function splitByPunctuation(text: string, maxChars: number): string[] {
  const pieces: string[] = []
  let rest = text
  while (rest.length > maxChars) {
    const window = rest.slice(0, maxChars)
    let cut = -1
    for (const mark of ['。', '！', '？', '；', '，', '、', '.', '!', '?', ';', ',']) {
      cut = Math.max(cut, window.lastIndexOf(mark))
    }
    if (cut < maxChars * 0.4) cut = maxChars - 1
    pieces.push(rest.slice(0, cut + 1).trim())
    rest = rest.slice(cut + 1).trim()
  }
  if (rest) pieces.push(rest)
  return pieces.filter((piece) => piece.length > 0)
}

/** 生成 SRT：序号从 1 开始，条目间空行 */
export function buildSrt(transcript: TranscriptItem[], duration: number): string {
  return buildCues(transcript, duration)
    .map((cue, index) => `${index + 1}\n${toSrtTime(cue.start)} --> ${toSrtTime(cue.end)}\n${cue.text}`)
    .join('\n\n')
}

/**
 * 生成 WebVTT：必须以 `WEBVTT` 开头。
 * 文件头注释里带标题与来源 —— VTT 允许注释，播放器会忽略，但下载后一眼能认出来源。
 */
export function buildVtt(transcript: TranscriptItem[], video: VideoInfo): string {
  const header = [
    'WEBVTT',
    '',
    `NOTE ${video.title}`,
    `NOTE 由 video-to-text 生成 ｜ 时长 ${formatDuration(video.duration)}`,
    '',
  ].join('\n')

  const body = buildCues(transcript, video.duration)
    .map((cue) => `${toVttTime(cue.start)} --> ${toVttTime(cue.end)}\n${cue.text}`)
    .join('\n\n')

  return `${header}${body}\n`
}
