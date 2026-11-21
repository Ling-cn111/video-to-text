import { NextResponse } from 'next/server'

/**
 * GET /api/capabilities（Mock）：演示模式恒为已配置，不阻塞 Mock 流程。
 * 真实模式由后端 /backend-api/capabilities 按环境变量与依赖可用性返回布尔状态。
 *
 * localAsrAvailable 在 Mock 下给 true：演示站要能展示完整的「本地 / 云端」选择器
 * （锁定项 §6 的默认值与说明行都在那里）。真实模式下线上 FC 镜像不含
 * faster-whisper，该值会是 false，选择器随之隐藏。
 *
 * fileUploadConfigured 在 Mock 下给 true（M3）：演示站要能展示「上传本地视频」
 * 入口。真实模式下由后端按 OSS_* 环境变量是否齐备返回，未配置时入口隐藏。
 */
export async function GET() {
  return NextResponse.json({
    cloudAsrConfigured: true,
    summarizeConfigured: true,
    localAsrAvailable: true,
    fileUploadConfigured: true,
  })
}
