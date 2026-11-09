import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { AgentCreateView } from "@/features/agents/agent-create-view";

export const metadata: Metadata = { title: "New agent" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <AgentCreateView />
    </Suspense>
  );
}
