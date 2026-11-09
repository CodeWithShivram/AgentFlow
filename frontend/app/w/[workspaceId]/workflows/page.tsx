import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { WorkflowsListView } from "@/features/workflows/workflows-list-view";

export const metadata: Metadata = { title: "Workflows" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <WorkflowsListView />
    </Suspense>
  );
}
