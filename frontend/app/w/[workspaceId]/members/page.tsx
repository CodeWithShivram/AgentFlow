import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { MembersView } from "@/features/workspace/members-view";

export const metadata: Metadata = { title: "Members" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <MembersView />
    </Suspense>
  );
}
