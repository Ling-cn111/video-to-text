import Link from 'next/link'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'

/**
 * 缺少任务参数时的兜底页（静态导出下 /processing、/result 无 ?id= 时）。
 * 静态导出不能调 notFound()（那需要服务端），所以用引导页代替。
 */
export function MissingTask({ title }: { title: string }) {
  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-16">
      <Card>
        <CardContent className="flex flex-col items-center gap-4 p-8 text-center">
          <h1 className="text-lg font-semibold">{title}</h1>
          <p className="text-sm text-muted-foreground">
            请从首页重新发起一次转写，或检查链接是否完整。
          </p>
          <Button asChild>
            <Link href="/">返回首页</Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
