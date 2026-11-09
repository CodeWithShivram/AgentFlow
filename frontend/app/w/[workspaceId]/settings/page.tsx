import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/common/states";
import { SettingsView } from "@/features/workspace/settings-view";

export const metadata: Metadata = { title: "Workspace settings" };

export default function Page() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <SettingsView />
    </Suspense>
  );
}
