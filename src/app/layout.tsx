import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { Toaster } from "@/components/ui/sonner";
import { BackendHealthBanner } from "@/components/layout/backend-health-banner";
import { PreferenceHydrator } from "@/components/layout/preference-hydrator";
import { SiteHeader } from "@/components/layout/site-header";
import { RegisterSw } from "@/components/pwa/register-sw";
import { supportedPlatformsShortText } from "@/lib/platforms/registry";
import "./globals.css";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-geist-sans",
  weight: "100 900",
});
const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
  weight: "100 900",
});

/** 站点描述：支持平台由构建能力决定（真实后端只接了 B站），不写死「B站 / 抖音」 */
const SITE_DESCRIPTION = `粘贴${supportedPlatformsShortText()}视频链接，自动生成带时间戳的文字稿与 AI 总结，支持导出 TXT / Markdown。`;

export const metadata: Metadata = {
  title: {
    default: "视频转文字 — 链接一键生成文字稿与 AI 总结",
    template: "%s · 视频转文字",
  },
  description: SITE_DESCRIPTION,
  appleWebApp: {
    capable: true,
    title: "视频转文字",
    statusBarStyle: "default",
  },
  icons: {
    apple: "/icons/apple-touch-icon.png",
  },
  // iOS Safari 会把疑似电话号码的字符串（如长串数字 ID）渲染成可拨号链接
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  themeColor: "#7c3aed",
  // 配合 globals.css 的 env(safe-area-inset-*)：刘海屏全屏时内容延伸到安全区外
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} flex min-h-screen flex-col antialiased`}
      >
        {/* 全局层：后端可达性横幅（fixed + 等高占位）与本地偏好水合 */}
        <BackendHealthBanner />
        <PreferenceHydrator />
        <SiteHeader />
        <main className="flex-1">{children}</main>
        <RegisterSw />
        <Toaster richColors position="top-center" />
      </body>
    </html>
  );
}
