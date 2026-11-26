import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { ProjectsListView } from "@/features/projects/projects-list-view";

export const metadata: Metadata = { title: "Projects" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <ProjectsListView />
    </Suspense>
  );
}
