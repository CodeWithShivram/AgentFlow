import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { ProjectCreateView } from "@/features/projects/project-create-view";

export const metadata: Metadata = { title: "New project" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <ProjectCreateView />
    </Suspense>
  );
}
