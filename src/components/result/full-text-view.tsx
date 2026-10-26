'use client'

import { useCallback, useMemo, useRef, useState } from 'react'
import { BookOpenText, ListTree, Search, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { FONT_SIZE_CLASS, FONT_SIZE_LABEL, READING_FONT_SIZES, useReadingFontSize } from '@/hooks/use-reading-font-size'
import type { ArticleSection } from '@/lib/article'
import { cn } from '@/lib/utils'

interface ArticleViewProps {
  /** 按章节分节后的段落（无章节时只有一节，title 为 null） */
  sections: ArticleSection[]
  /** 扁平段落：用于字数统计与「有内容才渲染」的判断，与导出同源 */
  paragraphs: string[]
}

/**
 * 全文阅读视图（无时间戳的文章式排版，由文字稿派生）。
 *
 * 相比首版新增三件事，都是长文阅读的真实痛点（P1-9）：
 * 1. 目录：有章节时给出可点击的章节列表，长文不必靠滚动找位置
 * 2. 搜索：只做「段落级命中 + 高亮」，不做跨段全文检索 —— 段落是本视图的最小单元，
 *    跨段高亮会把一句话拆到两个 mark 里，反而看不清
 * 3. 字号：三档，记忆在 localStorage
 *
 * 铁律（锁定项 §1 / §2）：本视图是「零时间戳」的阅读产物。章节标题来自 AI 总结，
 * 若标题本身含时间戳那是章节内容的事，但这里不额外渲染任何时间标记；
 * data-testid="fulltext-article" 只包住正文段落，目录 / 工具栏都在它外面，
 * 保证「页面正文 = 复制内容 = TXT 导出」三者逐段一致的断言不被工具 UI 污染。
 */
export function ArticleView({ sections, paragraphs }: ArticleViewProps) {
  const [fontSize, setFontSize] = useReadingFontSize()
  const [query, setQuery] = useState('')
  const [showToc, setShowToc] = useState(true)
  const sectionRefs = useRef<Array<HTMLHeadingElement | null>>([])

  const totalChars = useMemo(() => paragraphs.join('').length, [paragraphs])
  const readingMinutes = Math.max(1, Math.round(totalChars / 400))
  const normalizedQuery = query.trim().toLowerCase()

  /** 命中的段落下标集合：用于高亮 + 计数 */
  const matched = useMemo(() => {
    if (!normalizedQuery) return null
    const hits = new Set<string>()
    sections.forEach((section, sectionIndex) => {
      section.paragraphs.forEach((paragraph, paragraphIndex) => {
        if (paragraph.toLowerCase().includes(normalizedQuery)) {
          hits.add(`${sectionIndex}-${paragraphIndex}`)
        }
      })
    })
    return hits
  }, [normalizedQuery, sections])

  const hasChapters = sections.length > 1 || sections.some((section) => section.title !== null)

  const scrollToSection = useCallback((index: number) => {
    sectionRefs.current[index]?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [])

  return (
    <div className="flex flex-col gap-4">
      {/* 工具条：统计 / 字号 / 搜索。与正文分离，避免被误认为正文内容 */}
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <BookOpenText className="size-3.5 text-primary" aria-hidden />
          <span className="tabular-nums">
            {totalChars} 字 · 约 {readingMinutes} 分钟读完 · 按章节自动分段
          </span>
          <div className="ml-auto flex items-center gap-1" data-testid="article-font-size">
            <span className="mr-1">字号</span>
            {READING_FONT_SIZES.map((size) => (
              <Button
                key={size}
                type="button"
                variant={fontSize === size ? 'secondary' : 'ghost'}
                size="sm"
                className="h-7 min-w-8 px-2 text-xs"
                aria-pressed={fontSize === size}
                onClick={() => setFontSize(size)}
              >
                {FONT_SIZE_LABEL[size]}
              </Button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="在正文里查找…"
              aria-label="在正文里查找"
              data-testid="article-search"
              className="h-9 pl-8 pr-8 text-sm"
            />
            {query ? (
              <button
                type="button"
                onClick={() => setQuery('')}
                aria-label="清除查找"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <X className="size-3.5" aria-hidden />
              </button>
            ) : null}
          </div>
          {hasChapters ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-9 gap-1.5"
              aria-pressed={showToc}
              onClick={() => setShowToc((value) => !value)}
              data-testid="toggle-toc"
            >
              <ListTree className="size-3.5" aria-hidden />
              目录
            </Button>
          ) : null}
        </div>

        {matched ? (
          <p className="text-xs text-muted-foreground" data-testid="article-search-result">
            {matched.size > 0 ? `找到 ${matched.size} 段包含「${query.trim()}」` : `没有段落包含「${query.trim()}」`}
          </p>
        ) : null}
      </div>

      {/* 目录：只列出有标题的章节；点一下滚到对应小节 */}
      {hasChapters && showToc ? (
        <nav
          className="rounded-xl border bg-muted/30 p-3"
          aria-label="章节目录"
          data-testid="article-toc"
        >
          <p className="mb-2 text-xs font-medium text-muted-foreground">章节</p>
          <ol className="flex flex-col gap-1">
            {sections.map((section, index) =>
              section.title ? (
                <li key={`${section.title}-${index}`}>
                  <button
                    type="button"
                    onClick={() => scrollToSection(index)}
                    className="w-full truncate rounded-md px-2 py-1 text-left text-sm text-foreground/80 transition-colors hover:bg-background hover:text-foreground"
                  >
                    <span className="mr-1.5 tabular-nums text-muted-foreground">{index + 1}.</span>
                    {section.title}
                  </button>
                </li>
              ) : null,
            )}
          </ol>
        </nav>
      ) : null}

      <article className="mx-auto w-full max-w-3xl" data-testid="fulltext-article">
        {sections.map((section, sectionIndex) => (
          <section key={`section-${sectionIndex}`}>
            {section.title ? (
              <h2
                ref={(node) => {
                  sectionRefs.current[sectionIndex] = node
                }}
                className="mb-4 mt-8 scroll-mt-24 text-base font-semibold text-foreground first:mt-0 sm:text-lg"
              >
                {section.title}
              </h2>
            ) : null}
            {section.paragraphs.map((paragraph, paragraphIndex) => {
              const key = `${sectionIndex}-${paragraphIndex}`
              const isHit = matched?.has(key) ?? false
              return (
                <p
                  key={key}
                  className={cn(
                    'mb-6 text-justify text-foreground/90 last:mb-0',
                    FONT_SIZE_CLASS[fontSize],
                    // 搜索时非命中段落淡出：比只给命中段落加背景更容易扫读
                    matched && !isHit && 'opacity-40',
                    isHit && 'rounded-md bg-yellow-200/40 px-1 -mx-1 dark:bg-yellow-400/15',
                  )}
                >
                  {normalizedQuery ? highlight(paragraph, normalizedQuery) : paragraph}
                </p>
              )
            })}
          </section>
        ))}
      </article>
    </div>
  )
}

/** 把命中片段包成 <mark>：只影响样式，textContent 保持不变（复制/导出仍与页面一致） */
function highlight(text: string, query: string): React.ReactNode {
  if (!query) return text
  const lower = text.toLowerCase()
  const nodes: React.ReactNode[] = []
  let cursor = 0
  let found = lower.indexOf(query, cursor)
  let key = 0

  while (found !== -1) {
    if (found > cursor) nodes.push(text.slice(cursor, found))
    nodes.push(
      <mark
        key={`mark-${key}`}
        className="rounded bg-yellow-300/70 px-0.5 text-inherit dark:bg-yellow-400/30 dark:text-yellow-50"
      >
        {text.slice(found, found + query.length)}
      </mark>,
    )
    key += 1
    cursor = found + query.length
    found = lower.indexOf(query, cursor)
  }
  if (cursor < text.length) nodes.push(text.slice(cursor))
  return nodes
}
