import { NextResponse } from 'next/server'

/**
 * GET /api/health（Mock）：同源健康检查，恒 200 —— Mock 演示不依赖后端进程。
 * 真实模式由后端 GET /api/health 承担（前端经 /backend-api/health 同源代理），本路由不参与。
 */
export async function GET() {
  return NextResponse.json({ status: 'ok' })
}
