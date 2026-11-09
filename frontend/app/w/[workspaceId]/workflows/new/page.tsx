import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { WorkflowCreateView } from "@/features/workflows/workflow-create-view";

export const metadata: Metadata = { title: "New workflow" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <WorkflowCreateView />
    </Suspense>
  );
}
