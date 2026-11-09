import { Suspense } from "react";

import { FullPageSkeleton } from "@/components/shell/require-auth";
import { WorkspaceShell } from "@/components/shell/app-shell";

// The workspace ID is only known at request time, so the shell renders inside Suspense
// (Cache Components). Authentication and membership are resolved in the browser.
export default function WorkspaceLayout({ children }: LayoutProps<"/w/[workspaceId]">) {
  return (
    <Suspense fallback={<FullPageSkeleton />}>
      <WorkspaceShell>{children}</WorkspaceShell>
    </Suspense>
  );
}
