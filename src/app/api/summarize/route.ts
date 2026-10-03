import { NextResponse } from 'next/server'
import { summarizeTask } from '@/lib/mock-api'
import { readJsonBody, toErrorResponse } from '@/lib/api-server'

/** POST /api/summarize：生成 AI 总结 → { summary, keyPoints, chapters } */
export async function POST(request: Request) {
  try {
    const body = await readJsonBody<{ taskId?: string }>(request)
    const summary = await summarizeTask(body?.taskId ?? '')
    return NextResponse.json(summary)
  } catch (error) {
    return toErrorResponse(error)
  }
}
