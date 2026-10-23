import { Suspense } from "react";
import { ProcessingRoute } from "@/components/processing/processing-route";
import { ProcessingLoading } from "@/components/processing/processing-loading";

export const metadata = {
  title: "转写中",
};

/**
 * 进度页固定路由（/processing?id=<taskId>）。
 *
 * 不用动态段 [taskId]：桌面端静态导出无法枚举运行时生成的 taskId，
 * 查询参数形态在 web 与桌面端都能工作。旧链接 /processing/<id> 由
 * next.config.mjs 的 redirects 301 到本页面。
 *
 * useSearchParams 在预渲染时要求 Suspense 边界，否则构建报错。
 */
export default function ProcessingPage() {
  return (
    <Suspense fallback={<ProcessingLoading />}>
      <ProcessingRoute />
    </Suspense>
  );
}
