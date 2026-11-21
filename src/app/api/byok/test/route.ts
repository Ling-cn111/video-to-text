import { NextResponse } from 'next/server'
import { readJsonBody } from '@/lib/api-server'

/**
 * POST /api/byok/test（Mock）：与真实后端 /api/byok/test 同请求/响应形状。
 *
 * 失败注入约定（E2E 用）：模型名含「invalid」→ BYOK_KEY_INVALID 失败；
 * 其余恒成功（latencyMs 固定 123ms 便于断言）。Key 只进请求头，Mock 不读取。
 */
export async function POST(request: Request) {
  const body = await readJsonBody<{ llmBaseUrl?: string; llmModel?: string }>(request)
  const model = (body?.llmModel ?? '').trim()
  if (model.includes('invalid')) {
    return NextResponse.json({
      ok: false,
      code: 'BYOK_KEY_INVALID',
      message: 'API Key 被服务商拒绝（HTTP 401），请检查 Key 是否属于该服务商',
      latencyMs: 120,
    })
  }
  return NextResponse.json({ ok: true, latencyMs: 123, model: model || 'mock-model' })
}
