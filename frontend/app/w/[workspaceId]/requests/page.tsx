import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { RequestsView } from "@/features/interventions/requests-view";

export const metadata: Metadata = { title: "Requests" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <RequestsView />
    </Suspense>
  );
}
