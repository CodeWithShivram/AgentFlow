import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { ProjectDetailView } from "@/features/projects/project-detail-view";

export const metadata: Metadata = { title: "Project" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <ProjectDetailView />
    </Suspense>
  );
}
