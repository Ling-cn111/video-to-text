import { Suspense } from "react";
import { ResultRoute } from "@/components/result/result-route";
import { ResultLoading } from "@/components/result/result-loading";

export const metadata = {
  title: "转写结果",
};

/** 结果页固定路由（/result?id=<taskId>）；理由见 processing/page.tsx */
export default function ResultPage() {
  return (
    <Suspense fallback={<ResultLoading />}>
      <ResultRoute />
    </Suspense>
  );
}
