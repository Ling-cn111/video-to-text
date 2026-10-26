import type { Platform } from '@/lib/types'

/**
 * 最近转写记录（P1-7）。
 *
 * 为什么要做：taskId 此前只活在 URL 里 —— 关掉标签页 / 点「返回首页」之后，
 * 那份已经花了几分钟转写（甚至花了钱做 AI 总结）的结果就再也找不回来了。
 * 这是本项目最伤的一次性体验：用户付出的等待没有任何沉淀。
 *
 * 为什么只存元数据不存文字稿：文字稿可能有几百 KB，localStorage 配额只有 5MB 左右，
 * 存满之后整个域下的写入都会抛异常（包括 autoSummary / engine 偏好）。
 * 这里只留够渲染一张卡片的字段，点进去仍然按 taskId 从接口取（后端 TTL 7 天）。
 */

/** 记录状态：processing = 提交后还没跑完（进度页可继续跟），done = 已有结果 */
export type RecentTaskStatus = 'processing' | 'done'

export interface RecentTask {
  taskId: string
  title: string
  cover: string
  /** 秒 */
  duration: number
  platform: Platform
  /** 记录时刻（毫秒），用于排序与「刚刚 / N 小时前」展示 */
  savedAt: number
  status: RecentTaskStatus
}

/** localStorage 键。与 autoSummary / engine 并列，都是「不含隐私信息的用户偏好」 */
const RECENT_KEY = 'recentTasks'

/**
 * 保留条数。8 条 ≈ 一次会话能记住的量；再多首页会变成任务列表，
 * 与「粘贴链接就能用」的单一入口定位冲突。
 */
const RECENT_LIMIT = 8

/** 读取记录：任何异常（隐私模式 / 脏数据 / 旧版本结构）都退化成空列表 */
export function readRecentTasks(): RecentTask[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = window.localStorage.getItem(RECENT_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(isRecentTask).slice(0, RECENT_LIMIT)
  } catch {
    return []
  }
}

/**
 * 形状校验：宁可少显示一条，也不让脏数据把首页渲染崩掉。
 * 旧版本记录没有 status 字段 —— 那时只在完成时写入，因此缺省按 done 处理。
 */
function isRecentTask(value: unknown): value is RecentTask {
  if (!value || typeof value !== 'object') return false
  const task = value as Record<string, unknown>
  return (
    typeof task.taskId === 'string' &&
    task.taskId.length > 0 &&
    typeof task.title === 'string' &&
    typeof task.cover === 'string' &&
    typeof task.duration === 'number' &&
    typeof task.savedAt === 'number' &&
    (task.platform === 'bilibili' || task.platform === 'douyin')
  )
}

/**
 * 记录一次转写。同一 taskId 再次记录时**提到最前**而不是新增一条：
 * 提交时记一次（processing）、完成时再记一次（done），若不去重，
 * 首页很快就会被同一条任务占满两格。
 */
export function recordRecentTask(task: Omit<RecentTask, 'savedAt'>): void {
  if (typeof window === 'undefined') return
  try {
    const rest = readRecentTasks().filter((item) => item.taskId !== task.taskId)
    const next = [{ ...task, savedAt: Date.now() }, ...rest].slice(0, RECENT_LIMIT)
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next))
  } catch {
    // 配额受限 / 隐私模式：记录失败不影响主流程，只是首页少了入口
  }
}

/** 更新某条记录的状态（完成 / 失败时把 processing 改成 done，失败则整条移除） */
export function updateRecentTaskStatus(taskId: string, status: RecentTaskStatus): void {
  if (typeof window === 'undefined') return
  try {
    const items = readRecentTasks()
    const target = items.find((item) => item.taskId === taskId)
    if (!target || target.status === status) return
    const next = items.map((item) => (item.taskId === taskId ? { ...item, status } : item))
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next))
  } catch {
    // 同 recordRecentTask
  }
}

/**
 * 只在**没有记录时**补一条。
 *
 * 用于直链 / 刷新恢复：结果页每次挂载都会走 recover，若在那里无条件 record，
 * `savedAt` 会被刷成「刚刚」，首页的「N 小时前」就永远是刚刚 —— 时间信息失去意义。
 */
export function ensureRecentTask(task: Omit<RecentTask, 'savedAt'>): void {
  if (typeof window === 'undefined') return
  if (readRecentTasks().some((item) => item.taskId === task.taskId)) return
  recordRecentTask(task)
}

/**
 * 删除一条记录（任务已过期 / 点进去 404 时顺手清理）。
 * 不提供批量清空入口：这里只有 8 条，且过期项会自己消失。
 */
export function removeRecentTask(taskId: string): void {
  if (typeof window === 'undefined') return
  try {
    const next = readRecentTasks().filter((item) => item.taskId !== taskId)
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next))
  } catch {
    // 同上
  }
}

/**
 * 相对时间：只做到「刚刚 / N 分钟前 / N 小时前 / N 天前」四档。
 * 不显示具体时刻，因为用户关心的是「这是不是刚才那次」，而不是几点几分。
 */
export function formatRelativeTime(savedAt: number, now = Date.now()): string {
  const diffMs = Math.max(0, now - savedAt)
  const minutes = Math.floor(diffMs / 60_000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}
