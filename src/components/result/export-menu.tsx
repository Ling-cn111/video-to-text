'use client'

import { FileCode2, FileDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { exportResult } from '@/lib/export'
import type { Summary, TranscriptItem, VideoInfo } from '@/lib/types'

interface ExportMenuProps {
  video: VideoInfo
  transcript: TranscriptItem[]
  summary: Summary
}

/** 导出菜单：TXT / Markdown */
export function ExportMenu({ video, transcript, summary }: ExportMenuProps) {
  const handleExport = (format: 'txt' | 'md') => {
    exportResult(format, { video, transcript, summary })
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="gap-1.5">
          <FileDown className="size-3.5" aria-hidden />
          导出
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuLabel>导出文字稿</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => handleExport('txt')} className="gap-2">
          <FileDown className="size-4 text-muted-foreground" aria-hidden />
          纯文本（.txt）
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => handleExport('md')} className="gap-2">
          <FileCode2 className="size-4 text-muted-foreground" aria-hidden />
          Markdown（.md）
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
