"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { ErrorState } from "@/components/common/states";
import { Skeleton } from "@/components/ui/skeleton";
import { NetworkError } from "@/lib/api/errors";
import { useAuth } from "@/lib/auth/auth-provider";
import { loginHref } from "@/lib/navigation";

/** Renders children only for a signed-in user; otherwise sends the user to sign in. */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { status, sessionExpired, retry } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (status === "anonymous") {
      router.replace(loginHref(pathname, sessionExpired));
    }
  }, [status, sessionExpired, pathname, router]);

  if (status === "authenticated") return <>{children}</>;
  if (status === "unavailable") {
    return (
      <main className="mx-auto max-w-lg p-6">
        <ErrorState error={new NetworkError()} onRetry={retry} />
      </main>
    );
  }
  return <FullPageSkeleton />;
}

export function FullPageSkeleton() {
  return (
    <div className="flex min-h-svh" role="status" aria-label="Loading AgentFlow">
      <div className="hidden w-64 border-r p-4 md:block">
        <Skeleton className="mb-6 h-8 w-40" />
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="mb-2 h-7 w-full" />
        ))}
      </div>
      <div className="flex-1 space-y-4 p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    </div>
  );
}
