'use client'

import { useEffect, useState } from 'react'

/**
 * 读取当前 hostname（挂载后才可用）。
 *
 * 为什么不在渲染期直接读 `window`：服务端预渲染时没有 `window`，客户端首帧又必须
 * 与服务端 HTML 一致，渲染期读会触发 hydration mismatch。这里初始返回 `null`
 * （未知），挂载后填入真实值，调用方据此决定「先不渲染」还是「按环境选文案」。
 */
export function useHostname(): string | null {
  const [hostname, setHostname] = useState<string | null>(null)

  useEffect(() => {
    setHostname(window.location.hostname)
  }, [])

  return hostname
}
