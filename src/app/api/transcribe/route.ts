import { NextResponse } from 'next/server'
import { startTranscription } from '@/lib/mock-api'
import { readJsonBody, toErrorResponse } from '@/lib/api-server'
import type { VideoInfo } from '@/lib/types'

/** POST /api/transcribe：创建转写任务 → { taskId, status, stage, progress } */
export async function POST(request: Request) {
  try {
    const body = await readJsonBody<{ videoId?: string; video?: VideoInfo }>(request)
    const task = await startTranscription(body?.videoId ?? '', body?.video)
    return NextResponse.json(task)
  } catch (error) {
    return toErrorResponse(error)
  }
}
