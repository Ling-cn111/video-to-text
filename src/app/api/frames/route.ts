import { NextResponse } from 'next/server'
import { requestFrames } from '@/lib/mock-api'
import { readJsonBody, toErrorResponse } from '@/lib/api-server'
import type { FramesRequest } from '@/lib/types'

/** POST /api/frames（Mock）：重点帧抽帧请求 → { taskId, frames }。
 *  请求体与真实后端同形；失败注入约定（E2E 回归 40c）见 mock-api.ts 的 requestFrames
 *  （编造 taskId 带 fr:1 → frames.status=failed）。ready 随响应立即返回（无状态）。 */
export async function POST(request: Request) {
  try {
    const body = await readJsonBody<Partial<FramesRequest>>(request)
    const result = await requestFrames(body?.taskId ?? '', body?.timestamps ?? [])
    return NextResponse.json(result)
  } catch (error) {
    return toErrorResponse(error)
  }
}
