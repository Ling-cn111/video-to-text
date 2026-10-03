import { NextResponse } from 'next/server'
import { startTranscription } from '@/lib/mock-api'
import { readJsonBody, toErrorResponse } from '@/lib/api-server'

/** POST /api/transcribe：创建转写任务 → { taskId, status, stage, progress } */
export async function POST(request: Request) {
  try {
    const body = await readJsonBody<{ videoId?: string }>(request)
    const task = await startTranscription(body?.videoId ?? '')
    return NextResponse.json(task)
  } catch (error) {
    return toErrorResponse(error)
  }
}
