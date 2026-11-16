import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { ApprovalsView } from "@/features/interventions/approvals-view";

export const metadata: Metadata = { title: "Approval history" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <ApprovalsView />
    </Suspense>
  );
}
