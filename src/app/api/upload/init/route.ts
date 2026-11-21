import { NextResponse } from 'next/server'
import { initFileUpload } from '@/lib/mock-api'
import { readJsonBody, toErrorResponse } from '@/lib/api-server'
import type { UploadInitRequest } from '@/lib/types'

/**
 * POST /api/upload/init（Mock）：与真实后端 /api/upload/init 同请求/响应形状。
 * 返回的 uploadUrl 是假地址——Mock 客户端不会真发 PUT，仅保证契约链路可被断言。
 */
export async function POST(request: Request) {
  try {
    const body = await readJsonBody<UploadInitRequest>(request)
    const init = await initFileUpload({
      fileName: body?.fileName ?? '',
      size: body?.size ?? 0,
      mimeType: body?.mimeType ?? '',
    })
    return NextResponse.json(init)
  } catch (error) {
    return toErrorResponse(error)
  }
}
