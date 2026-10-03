'use client'

import { useEffect } from 'react'

/** 生产环境注册 Service Worker（开发环境跳过，避免缓存干扰） */
export function RegisterSw() {
  useEffect(() => {
    if (process.env.NODE_ENV !== 'production') return
    if (!('serviceWorker' in navigator)) return

    const register = () => {
      navigator.serviceWorker.register('/sw.js').catch((error) => {
        console.warn('[pwa] service worker 注册失败', error)
      })
    }

    if (document.readyState === 'complete') {
      void register()
      return
    }
    window.addEventListener('load', register, { once: true })
    return () => window.removeEventListener('load', register)
  }, [])

  return null
}
