import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { ProposalsView } from "@/features/agents/proposals-view";

export const metadata: Metadata = { title: "Agent proposals" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <ProposalsView />
    </Suspense>
  );
}
