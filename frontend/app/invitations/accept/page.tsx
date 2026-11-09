import type { Metadata } from "next";
import { Suspense } from "react";

import { PlainShell } from "@/components/shell/app-shell";
import { FullPageSkeleton, RequireAuth } from "@/components/shell/require-auth";
import { AcceptInvitationView } from "@/features/workspace/accept-invitation-view";

export const metadata: Metadata = { title: "Accept invitation" };

export default function AcceptInvitationPage() {
  return (
    <Suspense fallback={<FullPageSkeleton />}>
      <RequireAuth>
        <PlainShell>
          <AcceptInvitationView />
        </PlainShell>
      </RequireAuth>
    </Suspense>
  );
}
