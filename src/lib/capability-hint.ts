import { isLocalHostname } from '@/lib/banner-message'

/**
 * 「未配置 Key」类提示的文案选择（纯函数，便于 E2E 直接断言两条分支）。
 *
 * 和 `banner-message.ts` 是同一个教训：环境变量名、`setup.bat`、`backend/.env.local`
 * 这些词对**部署者**有用，对**线上访客**毫无意义——访客既没有仓库也没有脚本，
 * 看到「CLOUD_ASR_API_KEY」只会以为站点坏了。
 *
 * 所以同一处提示按访问环境分流：
 * - 本地（localhost / 127.0.0.1）：给出可执行的配置指引
 * - 公网：只说「暂不可用」，并把出路指向**用户自己能做的事**（BYOK 填自己的 Key），
 *   而不是让他去找一个他没有权限的部署者
 */

/** 云端 ASR 未配置时的提示（首页转写模式选择器下方） */
export function cloudUnconfiguredMessage(hostname: string): string {
  return isLocalHostname(hostname)
    ? '未配置云端 Key（CLOUD_ASR_API_KEY），云端模式不可用：请参考 README 或运行 setup.bat 配置，也可在右上角「API 设置」填写自己的 Key'
    : '云端模式暂不可用：请改用本地模式（免费、无需配置），或在右上角「API 设置」填写自己的 Key'
}

/** LLM 总结未配置时的提示（结果页总结区） */
export function summarizeUnconfiguredMessage(hostname: string): string {
  return isLocalHostname(hostname)
    ? '未配置 LLM Key，无法生成总结：请在 backend/.env.local 填写 DEEPSEEK_API_KEY 后重试，也可在右上角「API 设置」填写自己的 Key'
    : 'AI 总结暂不可用，文字稿与导出不受影响。可在右上角「API 设置」填写自己的 Key 后重试'
}
