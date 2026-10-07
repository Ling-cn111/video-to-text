import { NextResponse } from 'next/server'
import { summarizeTask } from '@/lib/mock-api'
import { readJsonBody, toErrorResponse } from '@/lib/api-server'
import type { SummarizeRequest } from '@/lib/types'

/** POST /api/summarize：生成 AI 总结（Mock）→ { summary, keyPoints, chapters }
 *  请求体与真实后端同形（transcript/title/duration 透传字段 Mock 忽略，只用 taskId）。 */
export async function POST(request: Request) {
  try {
    const body = await readJsonBody<Partial<SummarizeRequest>>(request)
    const summary = await summarizeTask(body?.taskId ?? '')
    return NextResponse.json(summary)
  } catch (error) {
    return toErrorResponse(error)
  }
}
