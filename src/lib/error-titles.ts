import type { ClientErrorCode } from '@/lib/api'

/**
 * 错误码 → 中文标题。
 *
 * 单独放在纯函数模块里（而不是塞在 error-alert.tsx 内），是为了让 E2E 能直接 import
 * 断言码表完整性：组件文件带 `'use client'` 且依赖 React，在测试进程里 import 会失败，
 * 而这张表恰恰是「后端加了码、前端忘了映射」最容易漏的地方。
 *
 * 用 `Record<ClientErrorCode, string>` 而不是 switch：新增错误码时 TypeScript 会直接
 * 报「缺少属性」，逼着这里同步，不会再出现「后端返回了新码、前端只显示『出了点问题』」。
 * 码表来源：`src/lib/types.ts` 的 ApiErrorCode（后端 `backend/app/errors.py` 为准）
 * + `src/lib/http.ts` 的客户端网络层码。
 */
export const ERROR_TITLES: Record<ClientErrorCode, string> = {
  // 解析 / 链接
  INVALID_URL: '链接无效',
  UNSUPPORTED_PLATFORM: '平台暂不支持',
  // 任务
  INVALID_TASK: '任务不存在',
  TASK_INTERRUPTED: '任务已中断',
  TASK_TIMEOUT: '处理超时',
  // 转写
  TRANSCRIBE_FAILED: '转写失败',
  AUDIO_TOO_LARGE: '音频过大',
  // 总结
  CLOUD_NOT_CONFIGURED: '未配置云端 ASR',
  SUMMARIZE_NOT_CONFIGURED: '未配置 AI 总结',
  TRANSCRIPT_TOO_LONG: '文字稿过长',
  SUMMARIZE_FAILED: '总结失败',
  // 限流（后端 POST 每分钟上限 RATE_LIMIT_PER_MINUTE，超出返回 429）
  RATE_LIMITED: '请求太频繁',
  // 用户自带的 Key（BYOK）被服务商拒绝：动作主体是用户自己，别让人去找部署者
  BYOK_KEY_INVALID: 'API Key 无效',
  // 用户自填的接口地址 / 模型名不合规：同样是用户自己去「API 设置」改
  BYOK_ENDPOINT_INVALID: '接口地址不可用',
  // 本地文件上传（M3）：前端预校验与后端共用同两个码，标题口径一致
  FILE_TOO_LARGE: '文件过大',
  FILE_FORMAT_UNSUPPORTED: '暂不支持该格式',
  // 站点没配 OSS：动作主体是部署者（与 CLOUD_NOT_CONFIGURED 同型）
  FILE_STORAGE_NOT_CONFIGURED: '本地上传未开启',
  // 预签名上传没完成就提交 / 对象已被清理：动作是「重新上传」
  FILE_NOT_FOUND: '上传未完成',
  // 重点帧（PR #89）：任务本身不支持抽帧 / 还没转写完；FRAMES_FAILED 只出现在
  // 任务详情 frames.error 里（帧卡片直接展示 message，标题作兜底）
  FRAMES_UNSUPPORTED: '该任务不支持重点帧',
  FRAMES_NOT_READY: '转写尚未完成',
  FRAMES_FAILED: '重点帧生成失败',
  // 兜底
  INTERNAL_ERROR: '服务异常',
  NETWORK_ERROR: '网络异常',
  UNKNOWN_ERROR: '出了点问题',
}

export function errorTitle(code: ClientErrorCode): string {
  return ERROR_TITLES[code] ?? '出了点问题'
}
