import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { WorkflowReportView } from "@/features/workflows/workflow-report";

export const metadata: Metadata = { title: "Execution report" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <WorkflowReportView />
    </Suspense>
  );
}
