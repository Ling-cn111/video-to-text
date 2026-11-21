import type { ChapterNote, KeyPoint } from '@/lib/types'

/**
 * 思维导图数据推导（PR18 基础版）：从总结的 chapters + keyPoints 拼出层级树。
 *
 * 为什么不从 LLM 单独要一份导图结构（增强留待后续）：现在的 Summary 里
 * chapters（title/note/timeStart/timeEnd）与 keyPoints（time/text）已经足够
 * 拼出三层树——根=视频标题、一级=章节、二级=落在该章节时间区间内的要点，
 * 不足处用章节 note 按句拆分补枝。零后端改动、零契约改动，旧缓存里已有的
 * summary 也能推导（summaryDetail 切换 / 刷新恢复自动跟随）。
 *
 * 归属规则是近似：keyPoints.time 落在 [timeStart, timeEnd) 即归入该章节；
 * 落点恰在边界的要点归入前一个区间重叠的章节（就近）。
 */

export interface MindmapNode {
  label: string
  children: MindmapNode[]
}

/** 按句拆分章节 note，作为章节的叶子说明（每章最多取 3 句，防止长 note 把树撑爆） */
function splitNoteSentences(note: string, max = 3): string[] {
  return note
    .split(/(?<=[。！？；;])/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .slice(0, max)
}

/** 把要点按时间就近归入章节；返回各章节的要点下标列表与未归入的要点 */
function assignKeyPoints(
  chapters: ChapterNote[],
  keyPoints: KeyPoint[],
): { perChapter: number[][]; orphans: KeyPoint[] } {
  const perChapter: number[][] = chapters.map(() => [])
  const orphans: KeyPoint[] = []
  keyPoints.forEach((point, index) => {
    const owner = chapters.findIndex((ch) => point.time >= ch.timeStart && point.time < ch.timeEnd)
    if (owner >= 0) perChapter[owner].push(index)
    else orphans.push(point)
  })
  return { perChapter, orphans }
}

/**
 * 构建思维导图树。
 *
 * @param title 视频标题（根节点）
 * @param chapters 章节笔记（可空数组）
 * @param keyPoints 核心要点（可空数组）
 */
export function buildMindmapTree(title: string, chapters: ChapterNote[], keyPoints: KeyPoint[]): MindmapNode {
  const root: MindmapNode = { label: title || '思维导图', children: [] }

  const sorted = [...chapters].sort((a, b) => a.timeStart - b.timeStart)
  const { perChapter, orphans } = assignKeyPoints(sorted, keyPoints)

  if (sorted.length === 0) {
    // 无章节：要点直接作为二级节点
    root.children = keyPoints.map((point) => ({ label: point.text, children: [] }))
    return root
  }

  sorted.forEach((chapter, index) => {
    const node: MindmapNode = { label: chapter.title, children: [] }
    // 要点优先（全片仅 3-7 条，归入后可能稀疏）
    for (const pointIndex of perChapter[index]) {
      node.children.push({ label: keyPoints[pointIndex].text, children: [] })
    }
    // note 拆句补枝：每章最多再取 3 句，且与要点文本去重
    const existing = new Set(node.children.map((child) => child.label))
    for (const sentence of splitNoteSentences(chapter.note)) {
      if (node.children.length >= 5) break
      if (existing.has(sentence)) continue
      node.children.push({ label: sentence, children: [] })
    }
    root.children.push(node)
  })

  // 时间戳落在所有章节区间之外的要点：挂在根上兜底，保证信息不丢
  for (const point of orphans) {
    root.children.push({ label: point.text, children: [] })
  }
  return root
}

/**
 * 树 → 缩进大纲文本（markdown）。
 *
 * 独立产物语义：与「文字稿 TXT/MD（全文件零时间戳）」「字幕 SRT/VTT」并列的
 * 第三类产物，由 export.ts 的独立函数消费；这里只做层级文本，不含时间戳。
 */
export function buildMindmapMarkdown(tree: MindmapNode): string {
  const lines: string[] = [`# ${tree.label}`, '']
  const walk = (node: MindmapNode, depth: number) => {
    for (const child of node.children) {
      lines.push(`${'  '.repeat(depth)}- ${child.label}`)
      walk(child, depth + 1)
    }
  }
  walk(tree, 0)
  return lines.join('\n').replace(/\n{3,}/g, '\n\n').trimEnd() + '\n'
}
