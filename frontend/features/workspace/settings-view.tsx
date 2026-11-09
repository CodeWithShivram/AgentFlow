"use client";

import { useQuery } from "@tanstack/react-query";

import { PageHeader } from "@/components/common/page-header";
import { RelativeTime } from "@/components/common/relative-time";
import { ErrorState, ListSkeleton } from "@/components/common/states";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { ROLE_LABELS } from "@/lib/roles";
import { useWorkspace } from "@/lib/workspace";

export function SettingsView() {
  const current = useWorkspace();
  const workspace = useQuery({
    queryKey: ["workspaces", current.id],
    queryFn: () => api.workspaces.get(current.id),
  });

  return (
    <div className="space-y-6">
      <PageHeader title="Workspace settings" />
      <Card>
        <CardHeader>
          <CardTitle>
            <h2>Details</h2>
          </CardTitle>
          <CardDescription>Renaming a workspace is not available yet: the API has no endpoint for it.</CardDescription>
        </CardHeader>
        <CardContent>
          {workspace.isPending ? (
            <ListSkeleton rows={3} label="Loading workspace" />
          ) : workspace.isError ? (
            <ErrorState error={workspace.error} onRetry={() => void workspace.refetch()} />
          ) : (
            <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[max-content_1fr]">
              <dt className="text-muted-foreground">Name</dt>
              <dd className="font-medium">{workspace.data.name}</dd>
              <dt className="text-muted-foreground">Workspace ID</dt>
              <dd className="font-mono text-xs break-all">{workspace.data.id}</dd>
              <dt className="text-muted-foreground">Your role</dt>
              <dd>{ROLE_LABELS[workspace.data.role]}</dd>
              <dt className="text-muted-foreground">Created</dt>
              <dd>
                <RelativeTime value={workspace.data.created_at} />
              </dd>
            </dl>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
