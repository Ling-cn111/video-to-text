'use client'

import { useEffect } from 'react'

/**
 * 根级错误边界（layout 自身崩溃时兜底）。
 *
 * 这个文件必须自带 <html>/<body> —— 出错时根布局已经渲染失败了，
 * 它不能依赖 layout.tsx 里的任何东西，样式也只能内联。
 * 因此这里刻意不引 Tailwind 类，避免样式表没加载时变成一坨纯文本。
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    // 生产环境 Next 已隐藏真实错误，只留 digest；留在控制台便于排查
    console.error('[global-error]', error)
  }, [error])

  return (
    <html lang="zh-CN">
      <body
        style={{
          margin: 0,
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontFamily:
            'system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif',
          background: '#ffffff',
          color: '#0a0a0a',
        }}
      >
        <div style={{ maxWidth: 420, padding: 24, textAlign: 'center' }}>
          <h1 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>应用未能启动</h1>
          <p style={{ fontSize: 14, color: '#525252', marginTop: 8, lineHeight: 1.6 }}>
            页面在加载最外层框架时就失败了，通常刷新一次即可恢复。
            {error.digest ? `（错误编号：${error.digest}）` : null}
          </p>
          <div style={{ marginTop: 20, display: 'flex', gap: 8, justifyContent: 'center' }}>
            <button
              onClick={reset}
              style={{
                padding: '8px 16px',
                fontSize: 14,
                borderRadius: 8,
                border: 'none',
                background: '#7c3aed',
                color: '#ffffff',
                cursor: 'pointer',
              }}
            >
              重试
            </button>
            <button
              onClick={() => {
                window.location.href = '/'
              }}
              style={{
                padding: '8px 16px',
                fontSize: 14,
                borderRadius: 8,
                border: '1px solid #d4d4d4',
                background: '#ffffff',
                color: '#0a0a0a',
                cursor: 'pointer',
              }}
            >
              返回首页
            </button>
          </div>
        </div>
      </body>
    </html>
  )
}
