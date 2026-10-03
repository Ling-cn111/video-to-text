import { NextResponse } from 'next/server'
import { MockApiError } from '@/lib/mock-api'
import type { ApiErrorCode, ApiErrorBody, AppError } from '@/lib/types'

/**
 * Route Handler 共享工具：把 MockApiError 映射为契约约定的 HTTP 状态码。
 */

const STATUS_BY_CODE: Record<ApiErrorCode, number> = {
  INVALID_URL: 400,
  UNSUPPORTED_PLATFORM: 422,
  INVALID_TASK: 404,
  TRANSCRIBE_FAILED: 500,
  FILE_TOO_LARGE: 413,
  FILE_FORMAT_UNSUPPORTED: 415,
  INTERNAL_ERROR: 500,
}

export function toErrorResponse(error: unknown): NextResponse<ApiErrorBody> {
  if (error instanceof MockApiError) {
    return NextResponse.json(
      { error: { code: error.code, message: error.message } satisfies AppError },
      { status: STATUS_BY_CODE[error.code] ?? 500 },
    )
  }
  return NextResponse.json(
    { error: { code: 'INTERNAL_ERROR', message: '服务开小差了，请稍后重试' } satisfies AppError },
    { status: 500 },
  )
}

export async function readJsonBody<T>(request: Request): Promise<T | null> {
  try {
    return (await request.json()) as T
  } catch {
    return null
  }
}
