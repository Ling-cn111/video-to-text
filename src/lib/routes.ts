/**
 * 应用内路由构造器（唯一出口，避免各处手拼字符串）。
 *
 * 为什么用查询参数而不是 `/[taskId]` 动态段：
 * Tauri 桌面端走 Next 静态导出（`output: 'export'`），动态段必须由
 * generateStaticParams 枚举出全部取值，而 taskId 是运行时生成的，无法枚举。
 * 查询参数在静态导出下由一个固定的 /processing、/result 页面在客户端读取，天然兼容。
 *
 * 兼容性：web 端仍在 next.config.mjs 里把旧的 /processing/xxx、/result/xxx
 * 重定向到新形式，老书签与外部链接不失效。
 */

/** 转写进度页：/processing?id=<taskId> */
export function processingHref(taskId: string): string {
  return `/processing?id=${encodeURIComponent(taskId)}`
}

/** 结果页：/result?id=<taskId> */
export function resultHref(taskId: string): string {
  return `/result?id=${encodeURIComponent(taskId)}`
}
