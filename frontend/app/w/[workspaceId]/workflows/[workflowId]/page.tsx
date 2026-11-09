import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { WorkflowDetailView } from "@/features/workflows/workflow-detail-view";

export const metadata: Metadata = { title: "Workflow" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <WorkflowDetailView />
    </Suspense>
  );
}
