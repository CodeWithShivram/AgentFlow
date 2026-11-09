import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { WorkspaceIndexRedirect } from "@/components/shell/redirects";

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <WorkspaceIndexRedirect />
    </Suspense>
  );
}
