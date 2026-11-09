"use client";

import { AlertTriangleIcon, InboxIcon, LockIcon, RefreshCwIcon, SearchXIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, describeError } from "@/lib/api/errors";
import { cn } from "@/lib/utils";

interface EmptyStateProps {
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  icon?: React.ReactNode;
  className?: string;
}

export function EmptyState({ title, description, action, icon, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed px-6 py-12 text-center",
        className,
      )}
    >
      <div className="text-muted-foreground" aria-hidden>
        {icon ?? <InboxIcon className="size-8" />}
      </div>
      <div className="space-y-1">
        <p className="font-medium">{title}</p>
        {description ? (
          <div className="max-w-md text-sm text-muted-foreground">{description}</div>
        ) : null}
      </div>
      {action}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  className?: string;
}) {
  const status = error instanceof ApiError ? error.status : 0;
  const title =
    status === 404 ? "Not found" : status === 403 ? "Access denied" : "Something went wrong";
  let icon = <AlertTriangleIcon className="size-8 text-destructive" />;
  if (status === 404) icon = <SearchXIcon className="size-8" />;
  if (status === 403) icon = <LockIcon className="size-8" />;
  const canRetry = onRetry !== undefined && status !== 403 && status !== 404;
  return (
    <div role="alert" className={className}>
      <EmptyState
        title={title}
        icon={icon}
        description={
          <>
            <p>{describeError(error)}</p>
            {error instanceof ApiError && error.requestId ? (
              <p className="mt-1 text-xs">Reference: {error.requestId}</p>
            ) : null}
          </>
        }
        action={
          canRetry ? (
            <Button variant="outline" onClick={onRetry}>
              <RefreshCwIcon data-icon="inline-start" />
              Try again
            </Button>
          ) : null
        }
      />
    </div>
  );
}

export function ListSkeleton({ rows = 5, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div className="space-y-2" role="status" aria-label={label}>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-12 w-full" />
      ))}
    </div>
  );
}

export function PageSkeleton() {
  return (
    <div className="space-y-6" role="status" aria-label="Loading page">
      <div className="space-y-2">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-4 w-80 max-w-full" />
      </div>
      <ListSkeleton rows={4} />
    </div>
  );
}
