'use client'

import { ThemeProvider as NextThemesProvider } from 'next-themes'
import type { ComponentProps } from 'react'

/**
 * 主题容器：globals.css 里 `.dark` 那套变量早就写好了，但一直没挂 Provider，
 * 所以永远停在浅色。attribute="class" 让 next-themes 往 <html> 加 `.dark` 类。
 *
 * enableSystem：默认跟随系统，用户没主动选过时不硬塞浅色。
 * disableTransitionOnChange：切换瞬间禁掉 CSS 过渡，否则整页颜色渐变会闪。
 */
export function ThemeProvider({ children, ...props }: ComponentProps<typeof NextThemesProvider>) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
      {...props}
    >
      {children}
    </NextThemesProvider>
  )
}
