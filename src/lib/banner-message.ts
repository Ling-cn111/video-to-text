/**
 * 「后端不可达」横幅的文案选择（纯函数，便于在 E2E 里直接断言两条分支）。
 *
 * 为什么要分环境：本地开发者看到「运行 run.bat start」是对的，但公网部署的访客
 * 根本没有这个脚本——上线初期曾因此让一个能用的站点看起来像坏了。
 */

/** 本机地址判定：只有这些 hostname 才提示 run.bat */
export function isLocalHostname(hostname: string): boolean {
  return (
    hostname === 'localhost' ||
    hostname === '127.0.0.1' ||
    hostname === '[::1]' ||
    hostname === '::1'
  )
}

/** 横幅正文：本地给启动指引，公网只提示稍后重试 */
export function bannerMessage(hostname: string): string {
  return isLocalHostname(hostname)
    ? '后端服务不可达，请确认已启动本地服务（运行 run.bat start）'
    : '后端服务不可达，请稍后重试'
}
