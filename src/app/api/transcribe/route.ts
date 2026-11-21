import { NextResponse } from 'next/server'
import { startTranscription } from '@/lib/mock-api'
import { readJsonBody, toErrorResponse } from '@/lib/api-server'
import type { FileRef, VideoInfo } from '@/lib/types'

/**
 * POST /api/transcribe：创建转写任务 → { taskId, status, stage, progress }。
 * M3：透传 kind/file（file 任务）与 url/engine/hotwords（链接任务），
 * Mock 服务端按 kind 分流；字段全部照传，保证「带上就是带上了」可被 E2E 断言。
 */
export async function POST(request: Request) {
  try {
    const body = await readJsonBody<{
      videoId?: string
      kind?: 'url' | 'file'
      url?: string
      file?: FileRef
      video?: VideoInfo
      engine?: 'local' | 'cloud'
      hotwords?: string[]
    }>(request)
    const task = await startTranscription(body?.videoId ?? '', body?.video, {
      kind: body?.kind,
      file: body?.file,
    })
    return NextResponse.json(task)
  } catch (error) {
    return toErrorResponse(error)
  }
}
