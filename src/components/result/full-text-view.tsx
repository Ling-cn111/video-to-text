import { BookOpenText } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface FullTextViewProps {
  paragraphs: string[]
}

/** 全文阅读：无时间戳的文章式排版（段落由文字稿派生） */
export function FullTextView({ paragraphs }: FullTextViewProps) {
  const totalChars = paragraphs.join('').length
  const readingMinutes = Math.max(1, Math.round(totalChars / 400))

  return (
    <Card>
      <CardHeader className="pb-4">
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <BookOpenText className="size-4 text-primary" aria-hidden />
          全文阅读
          <Badge variant="secondary" className="ml-auto font-normal tabular-nums">
            {totalChars} 字 · 约 {readingMinutes} 分钟读完
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <article className="mx-auto max-w-3xl">
          {paragraphs.map((paragraph, index) => (
            <p
              key={index}
              className="mb-6 text-justify text-[15px] leading-[1.9] text-foreground/90 last:mb-0 sm:text-base"
            >
              {paragraph}
            </p>
          ))}
        </article>
      </CardContent>
    </Card>
  )
}
