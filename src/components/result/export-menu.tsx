'use client'

import { Captions, FileCode2, FileDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { exportResult, exportSubtitles } from '@/lib/export'
import type { TranscriptItem, VideoInfo } from '@/lib/types'

interface ExportMenuProps {
  video: VideoInfo
  /** 无时间戳的自然段落（formatTranscriptToArticle 派生） */
  paragraphs: string[]
  /** 句级文字稿：字幕导出需要原始时间戳 */
  transcript: TranscriptItem[]
}

/**
 * 导出菜单，两组产物：
 * - 导出全文（TXT / MD）：阅读用，全文件零时间戳（锁定项 §2）
 * - 导出字幕（SRT / VTT）：挂到播放器用，必须带时间轴
 * 分组是必要的：用户看到「导出」时想的可能是任一用途，混在一个列表里
 * 很容易下错文件（拿到一份没有时间戳的「字幕」）。
 */
export function ExportMenu({ video, paragraphs, transcript }: ExportMenuProps) {
  const handleExport = (format: 'txt' | 'md') => {
    exportResult(format, { video, paragraphs })
  }

  const handleSubtitle = (format: 'srt' | 'vtt') => {
    exportSubtitles(format, { video, transcript })
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="gap-1.5" title="导出全文或字幕文件">
          <FileDown className="size-3.5" aria-hidden />
          导出
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel>导出全文</DropdownMenuLabel>
        <p className="px-2 pb-1.5 text-xs text-muted-foreground">纯文本 · 不含时间戳</p>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => handleExport('txt')} className="gap-2" data-testid="export-txt">
          <FileDown className="size-4 text-muted-foreground" aria-hidden />
          纯文本（.txt）
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => handleExport('md')} className="gap-2" data-testid="export-md">
          <FileCode2 className="size-4 text-muted-foreground" aria-hidden />
          Markdown（.md）
        </DropdownMenuItem>

        <DropdownMenuSeparator />
        <DropdownMenuLabel>导出字幕</DropdownMenuLabel>
        <p className="px-2 pb-1.5 text-xs text-muted-foreground">带时间轴 · 可直接挂到播放器</p>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => handleSubtitle('srt')} className="gap-2" data-testid="export-srt">
          <Captions className="size-4 text-muted-foreground" aria-hidden />
          字幕（.srt）
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => handleSubtitle('vtt')} className="gap-2" data-testid="export-vtt">
          <Captions className="size-4 text-muted-foreground" aria-hidden />
          字幕（.vtt）
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
