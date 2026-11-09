import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { ActivityView } from "@/features/workspace/activity-view";

export const metadata: Metadata = { title: "Activity" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <ActivityView />
    </Suspense>
  );
}
