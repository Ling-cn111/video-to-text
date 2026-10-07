import { NextResponse } from 'next/server'

/**
 * GET /api/capabilities（Mock）：演示模式恒为已配置，不阻塞 Mock 流程。
 * 真实模式由后端 /backend-api/capabilities 按环境变量返回布尔状态。
 */
export async function GET() {
  return NextResponse.json({ cloudAsrConfigured: true, summarizeConfigured: true })
}
