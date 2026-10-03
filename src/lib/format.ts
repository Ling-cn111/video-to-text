/** 时间格式化：秒 → mm:ss 或 h:mm:ss（>1 小时） */
export function formatTimestamp(totalSeconds: number): string {
  const total = Math.max(0, Math.floor(totalSeconds))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  const mm = String(minutes).padStart(2, '0')
  const ss = String(seconds).padStart(2, '0')
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`
}

/** 时长展示与时间戳同规则 */
export const formatDuration = formatTimestamp

/** 语言环境日期时间（用于导出文件头） */
export function formatDateTime(timestamp = Date.now()): string {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(timestamp))
}
