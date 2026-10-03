import type { Chapter, TranscriptItem } from '@/lib/types'

/** 兜底分段阈值：至少 3 句且累计约 160 字，且停在句末标点 */
const MIN_PARAGRAPH_CHARS = 160
const MIN_PARAGRAPH_ITEMS = 3
const SENTENCE_END = /[。！？!?…”」』)]$/

/**
 * 把带时间戳的文字稿整理成无时间戳的自然段落（「全文阅读」模式的数据来源）。
 *
 * - 传入章节时：按章节时间点分段，段落语义与「章节笔记」对齐
 * - 未传章节时：按句长兜底（累计约 160 字且停在句末标点处切分）
 * - 段内文本按原顺序直接拼接（中文语句自带句末标点）
 */
export function formatTranscriptToArticle(
  transcript: TranscriptItem[],
  chapters?: Chapter[],
): string[] {
  if (transcript.length === 0) return []

  const chapterStarts = (chapters ?? [])
    .map((chapter) => chapter.time)
    .sort((a, b) => a - b)

  const paragraphs: string[] = []
  let parts: string[] = []
  let charsInParagraph = 0
  let itemsInParagraph = 0
  let chapterCursor = 0
  let prevTime = 0

  const flush = () => {
    const text = parts.join('').trim()
    if (text) paragraphs.push(text)
    parts = []
    charsInParagraph = 0
    itemsInParagraph = 0
  }

  transcript.forEach((item, index) => {
    if (index > 0) {
      let crossedChapter = false
      while (chapterCursor < chapterStarts.length && chapterStarts[chapterCursor] <= item.time) {
        if (chapterStarts[chapterCursor] > prevTime) crossedChapter = true
        chapterCursor += 1
      }

      const previousText = (parts.at(-1) ?? '').trim()
      const sentenceEnded = SENTENCE_END.test(previousText)
      const fallbackCut =
        chapterStarts.length === 0 &&
        itemsInParagraph >= MIN_PARAGRAPH_ITEMS &&
        charsInParagraph >= MIN_PARAGRAPH_CHARS &&
        sentenceEnded

      if (crossedChapter || fallbackCut) flush()
    }

    parts.push(item.text.trim())
    charsInParagraph += item.text.length
    itemsInParagraph += 1
    prevTime = item.time
  })
  flush()

  return paragraphs
}
