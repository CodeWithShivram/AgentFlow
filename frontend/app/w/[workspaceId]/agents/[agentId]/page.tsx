import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { AgentDetailView } from "@/features/agents/agent-detail-view";

export const metadata: Metadata = { title: "Agent" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <AgentDetailView />
    </Suspense>
  );
}
