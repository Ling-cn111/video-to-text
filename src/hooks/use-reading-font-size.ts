'use client'

import { useCallback, useEffect, useState } from 'react'

/**
 * 阅读偏好（P1-9）：正文字号三档。
 *
 * 为什么存 localStorage：读长文是「一次性投入很久」的动作，用户调到舒服的字号后
 * 下次打开另一篇还应该保持；只在内存里存等于每次重来。
 *
 * 为什么只有三档而不是连续滑块：连续调节需要即时预览，滑块拖动的每一帧都要重排
 * 整篇文章，长文下会卡；三档足够覆盖「字太小 / 正好 / 想更大」这三种真实诉求。
 */

export type ReadingFontSize = 'sm' | 'md' | 'lg'

const FONT_SIZE_KEY = 'readingFontSize'

export const READING_FONT_SIZES: readonly ReadingFontSize[] = ['sm', 'md', 'lg'] as const

/** 三档对应的正文样式（行高随字号一起调，否则小字显得松散、大字显得拥挤） */
export const FONT_SIZE_CLASS: Record<ReadingFontSize, string> = {
  sm: 'text-[14px] leading-[1.85]',
  md: 'text-[15px] leading-[1.9] sm:text-base',
  lg: 'text-[17px] leading-[2] sm:text-lg',
}

export const FONT_SIZE_LABEL: Record<ReadingFontSize, string> = {
  sm: '小',
  md: '中',
  lg: '大',
}

function isFontSize(value: unknown): value is ReadingFontSize {
  return value === 'sm' || value === 'md' || value === 'lg'
}

export function readReadingFontSize(): ReadingFontSize {
  if (typeof window === 'undefined') return 'md'
  try {
    const stored = window.localStorage.getItem(FONT_SIZE_KEY)
    return isFontSize(stored) ? stored : 'md'
  } catch {
    return 'md'
  }
}

export function persistReadingFontSize(size: ReadingFontSize): void {
  try {
    window.localStorage.setItem(FONT_SIZE_KEY, size)
  } catch {
    // 隐私模式 / 存储被禁用：本次会话仍然生效，只是不记住
  }
}

/** 字号偏好：默认「中」，挂载后读取本地值（避免 SSR/CSR 首帧不一致） */
export function useReadingFontSize(): [ReadingFontSize, (size: ReadingFontSize) => void] {
  const [size, setSize] = useState<ReadingFontSize>('md')

  useEffect(() => {
    setSize(readReadingFontSize())
  }, [])

  const update = useCallback((next: ReadingFontSize) => {
    setSize(next)
    persistReadingFontSize(next)
  }, [])

  return [size, update]
}
