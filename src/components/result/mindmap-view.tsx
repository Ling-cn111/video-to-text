'use client'

import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'

import { ScrollArea } from '@/components/ui/scroll-area'
import type { MindmapNode } from '@/lib/mindmap'

/**
 * 思维导图渲染（PR18 基础版）：手写缩进树，零第三方依赖。
 *
 * 形态：根节点在顶部，子树缩进展开（Chevron 切换折叠），层级由缩进线表达；
 * 深色模式跟随主题变量（纯 Tailwind token，无硬编码颜色）。
 * 渲染为纯客户端组件（与 Dialog 同模式）：本组件只在结果页 summary 就绪后挂载。
 *
 * 交互约束（一期范围）：展开 / 折叠子树；不做缩放拖拽（markmap 留作增强）。
 */
function MindmapNodeRow({ node, depth }: { node: MindmapNode; depth: number }) {
  const [expanded, setExpanded] = useState(true)
  const hasChildren = node.children.length > 0

  return (
    <li>
      <div
        className="flex items-center gap-1 py-1"
        style={{ paddingLeft: `${depth * 20}px` }}
        data-testid="mindmap-node"
      >
        {hasChildren ? (
          <button
            type="button"
            onClick={() => setExpanded((prev) => !prev)}
            aria-expanded={expanded}
            aria-label={`${expanded ? '收起' : '展开'} ${node.label}`}
            className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            {expanded ? (
              <ChevronDown className="size-4" aria-hidden />
            ) : (
              <ChevronRight className="size-4" aria-hidden />
            )}
          </button>
        ) : (
          <span className="size-4 shrink-0" aria-hidden />
        )}
        <span
          className={
            depth === 0
              ? 'text-sm font-semibold'
              : depth === 1
                ? 'text-sm font-medium'
                : 'text-xs text-muted-foreground'
          }
        >
          {node.label}
        </span>
      </div>
      {hasChildren && expanded ? (
        <ul role="group">
          {node.children.map((child, index) => (
            <MindmapNodeRow key={`${depth}-${index}-${child.label}`} node={child} depth={depth + 1} />
          ))}
        </ul>
      ) : null}
    </li>
  )
}

export function MindmapView({ tree }: { tree: MindmapNode }) {
  // 展开状态挂在节点行内部（按位置记忆）；树随 summary 更新重建时整体回到展开态
  const nodeCount = useMemo(() => {
    let count = 0
    const walk = (node: MindmapNode) => {
      count += 1
      for (const child of node.children) walk(child)
    }
    walk(tree)
    return count
  }, [tree])

  return (
    <ScrollArea className="max-h-[28rem] pr-3" data-testid="mindmap-view">
      <ul className="space-y-0.5" role="tree" aria-label="思维导图">
        <MindmapNodeRow node={tree} depth={0} />
      </ul>
      <p className="mt-2 px-1 text-[11px] text-muted-foreground" data-testid="mindmap-meta">
        共 {nodeCount} 个节点 · 由 AI 总结的章节与要点推导生成
      </p>
    </ScrollArea>
  )
}
