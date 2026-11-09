import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { AgentsListView } from "@/features/agents/agents-list-view";

export const metadata: Metadata = { title: "Agents" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <AgentsListView />
    </Suspense>
  );
}
