import type { MetadataRoute } from 'next'

/** PWA manifest：由 Next 构建时生成到 /manifest.webmanifest */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: '视频转文字 — 链接一键生成文字稿与 AI 总结',
    short_name: '视频转文字',
    description:
      '粘贴 B站 / 抖音视频链接，自动生成带时间戳的文字稿与 AI 总结，支持导出 TXT / Markdown。',
    id: '/',
    start_url: '/',
    scope: '/',
    display: 'standalone',
    // 支持的浏览器优先用 standalone；不支持时回退（旧版 Android Chrome 认这个字段）
    display_override: ['standalone', 'minimal-ui'],
    orientation: 'portrait-primary',
    lang: 'zh-CN',
    background_color: '#ffffff',
    theme_color: '#7c3aed',
    categories: ['productivity', 'utilities'],
    // 长按桌面图标弹出的快捷入口
    shortcuts: [
      {
        name: '开始转写',
        short_name: '转写',
        description: '粘贴视频链接，生成文字稿',
        url: '/',
        icons: [{ src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' }],
      },
    ],
    icons: [
      { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
      { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
      {
        src: '/icons/icon-maskable-512.png',
        sizes: '512x512',
        type: 'image/png',
        purpose: 'maskable',
      },
    ],
  }
}
