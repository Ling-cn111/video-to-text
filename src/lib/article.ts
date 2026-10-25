import type { ChapterNote, TranscriptItem } from '@/lib/types'

/** 兜底分段阈值：至少 3 句且累计约 160 字，且停在句末标点 */
const MIN_PARAGRAPH_CHARS = 160
const MIN_PARAGRAPH_ITEMS = 3
/** 超长段落切分阈值：章节缺位时避免整篇挤成一段 */
const MAX_PARAGRAPH_CHARS = 500
const SENTENCE_END = /[。！？!?…”」』)]$/
const SENTENCE_END_CHARS = "。！？!?…"

/** 一篇文章小节：章节标题 + 该章节的段落（无章节时只有一节，标题为 null） */
export interface ArticleSection {
  /** 章节标题；未提供章节时为 null */
  title: string | null
  /** 章节起始秒数（用于「跳到本节」定位）；未提供章节时为 null */
  timeStart: number | null
  paragraphs: string[]
}

/**
 * 把带时间戳的文字稿整理成无时间戳的自然段落，并按章节分节（「全文阅读」的数据来源）。
 *
 * - 传入章节时：按章节时间点分节，段落语义与「章节笔记」对齐，同时可用于生成目录
 * - 未传章节时：按句长兜底（累计约 160 字且停在句末标点处切分），只有一节
 * - 段内文本按原顺序直接拼接（中文语句自带句末标点）
 *
 * 分节是「附加信息」而不是「重新分段」：把各节段落按顺序拼接，结果与旧版
 * `formatTranscriptToArticle` 完全一致（导出与复制依赖这一点）。
 */
export function formatTranscriptToSections(
  transcript: TranscriptItem[],
  chapters?: ChapterNote[],
): ArticleSection[] {
  if (transcript.length === 0) return []

  // 章节按时间排序：LLM 输出的章节顺序不保证递增
  const orderedChapters = [...(chapters ?? [])].sort((a, b) => a.timeStart - b.timeStart)
  const chapterStarts = orderedChapters.map((chapter) => chapter.timeStart)

  const sections: ArticleSection[] =
    orderedChapters.length > 0
      ? orderedChapters.map((chapter) => ({
          title: chapter.title?.trim() || null,
          timeStart: chapter.timeStart,
          paragraphs: [],
        }))
      : [{ title: null, timeStart: null, paragraphs: [] }]

  let parts: string[] = []
  let charsInParagraph = 0
  let itemsInParagraph = 0
  let chapterCursor = 0
  let prevTime = 0
  /** 当前段落归属的小节下标（章节起点之前的开头部分归入第一节） */
  let sectionIndex = 0

  const flush = () => {
    const text = parts.join('').trim()
    if (text) sections[sectionIndex]?.paragraphs.push(...splitLongParagraph(text))
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

      if (crossedChapter || fallbackCut) {
        flush()
        // 跨章：本段之后的条目属于新章节；chapterCursor 已指向下一章的下标
        if (crossedChapter) sectionIndex = Math.min(Math.max(chapterCursor - 1, 0), sections.length - 1)
      }
    }

    parts.push(item.text.trim())
    charsInParagraph += item.text.length
    itemsInParagraph += 1
    prevTime = item.time
  })
  flush()

  return sections
}

/** 只要扁平段落（导出 / 复制 / 页面正文都以此为准） */
export function formatTranscriptToArticle(
  transcript: TranscriptItem[],
  chapters?: ChapterNote[],
): string[] {
  return formatTranscriptToSections(transcript, chapters).flatMap((section) => section.paragraphs)
}

/** 超长段落按句末标点切分（真实转写无章节可用时保证阅读分段） */
function splitLongParagraph(text: string): string[] {
  if (text.length <= MAX_PARAGRAPH_CHARS) return [text]

  const pieces: string[] = []
  let rest = text
  while (rest.length > MAX_PARAGRAPH_CHARS) {
    const window = rest.slice(0, MAX_PARAGRAPH_CHARS)
    let cut = -1
    for (const ch of SENTENCE_END_CHARS) {
      cut = Math.max(cut, window.lastIndexOf(ch))
    }
    if (cut < MAX_PARAGRAPH_CHARS * 0.4) cut = MAX_PARAGRAPH_CHARS - 1 // 窗口内无句末标点时硬切
    pieces.push(rest.slice(0, cut + 1).trim())
    rest = rest.slice(cut + 1).trim()
  }
  if (rest) pieces.push(rest)
  return pieces.filter((piece) => piece.length > 0)
}
