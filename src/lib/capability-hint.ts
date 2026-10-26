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
 * - 公网：只说「暂不可用」，并把出路指向**用户自己能做的事**
 *
 * 注意两类提示的出路不同，不能互相抄：
 * - 云端 ASR 用的是站点的免费模型，**界面不收集用户的 ASR Key**，
 *   所以公网分支只能建议改用别的转写路径，不能让他去填 Key。
 * - AI 总结是用户自己的 Key（BYOK），公网分支必须指向右上角「API 设置」，
 *   那是用户唯一能自己走通的路。
 */

/** 云端 ASR 未配置时的提示（仅自建部署会出现：那里才有「本地 / 云端」选择器） */
export function cloudUnconfiguredMessage(hostname: string): string {
  return isLocalHostname(hostname)
    ? '未配置云端转写（CLOUD_ASR_API_KEY），云端模式不可用：请参考 README 或运行 setup.bat 配置，或直接使用本地模式'
    : '云端转写暂不可用，请改用本地模式'
}

/** LLM 总结未配置时的提示（结果页总结区） */
export function summarizeUnconfiguredMessage(hostname: string): string {
  return isLocalHostname(hostname)
    ? '未配置 AI 总结 Key，无法生成总结：请在 backend/.env.local 填写 DEEPSEEK_API_KEY 后重试，也可在右上角「API 设置」填写自己的 Key'
    : 'AI 总结需要你自己的 API Key：点右上角「API 设置」填写后即可生成。文字稿与导出不受影响'
}
