import type { Metadata } from "next";
import { Suspense } from "react";

import { PlainShell } from "@/components/shell/app-shell";
import { FullPageSkeleton, RequireAuth } from "@/components/shell/require-auth";
import { AccountView } from "@/features/workspace/account-view";

export const metadata: Metadata = { title: "Account" };

export default function AccountPage() {
  return (
    <Suspense fallback={<FullPageSkeleton />}>
      <RequireAuth>
        <PlainShell>
          <AccountView />
        </PlainShell>
      </RequireAuth>
    </Suspense>
  );
}
