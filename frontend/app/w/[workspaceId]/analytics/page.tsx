import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { AnalyticsView } from "@/features/analytics/analytics-view";

export const metadata: Metadata = { title: "Analytics" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <AnalyticsView />
    </Suspense>
  );
}
