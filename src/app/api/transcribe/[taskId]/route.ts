import { NextResponse } from 'next/server'
import { getTask } from '@/lib/mock-api'
import { toErrorResponse } from '@/lib/api-server'

/** GET /api/transcribe/:taskId：轮询任务状态，完成后返回文字稿 */
export async function GET(_request: Request, { params }: { params: { taskId: string } }) {
  try {
    const task = await getTask(params.taskId)
    return NextResponse.json(task)
  } catch (error) {
    return toErrorResponse(error)
  }
}
