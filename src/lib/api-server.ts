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
  CLOUD_NOT_CONFIGURED: 400,
  SUMMARIZE_NOT_CONFIGURED: 400,
  TRANSCRIPT_TOO_LONG: 413,
  AUDIO_TOO_LARGE: 413,
  SUMMARIZE_FAILED: 502,
  TASK_INTERRUPTED: 409,
  TASK_TIMEOUT: 500,
  // 限流（后端 backend/app/services/rate_limit.py 的中间件产生，非 Mock 路径；
  // 此处补齐映射只为让 `Record<ApiErrorCode, number>` 穷举成立，Mock 永不抛该码）
  RATE_LIMITED: 429,
  // 用户自带的 Key 被上游拒绝（后端 backend/app/errors.py 的 ByokKeyInvalidError）；
  // Mock 路径永不产生该码，此处补齐映射只为让穷举成立
  BYOK_KEY_INVALID: 401,
  // 用户自填的接口地址 / 模型名不合规（后端 backend/app/errors.py 的
  // ByokEndpointInvalidError）；同样只在真实后端出现，此处补齐映射让穷举成立
  BYOK_ENDPOINT_INVALID: 400,
  // 本地文件上传（M3）：FILE_* 由真实后端产生（FILE_TOO_LARGE / FILE_FORMAT_UNSUPPORTED
  // 也会由前端本地预校验直接抛出，不走 HTTP）；FILE_NOT_FOUND / FILE_STORAGE_NOT_CONFIGURED
  // 为真实后端专用，Mock 路径不产生，此处补齐映射让穷举成立
  FILE_TOO_LARGE: 413,
  FILE_FORMAT_UNSUPPORTED: 422,
  FILE_STORAGE_NOT_CONFIGURED: 400,
  FILE_NOT_FOUND: 404,
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
