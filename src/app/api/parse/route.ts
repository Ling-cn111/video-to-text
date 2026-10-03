import { NextResponse } from 'next/server'
import { parseVideoUrl } from '@/lib/mock-api'
import { readJsonBody, toErrorResponse } from '@/lib/api-server'

/** POST /api/parse：解析视频链接 → VideoInfo */
export async function POST(request: Request) {
  try {
    const body = await readJsonBody<{ url?: string }>(request)
    const video = await parseVideoUrl(body?.url ?? '')
    return NextResponse.json(video)
  } catch (error) {
    return toErrorResponse(error)
  }
}
