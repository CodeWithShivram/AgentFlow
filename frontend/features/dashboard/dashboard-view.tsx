"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { ArrowRightIcon, PlusIcon } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/common/page-header";
import { RelativeTime } from "@/components/common/relative-time";
import { ErrorState, ListSkeleton } from "@/components/common/states";
import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { WORKFLOW_STATUSES, type WorkflowStatus } from "@/lib/api/types";
import { formatMoney, humanize } from "@/lib/format";
import { workspacePath } from "@/lib/navigation";
import { useCan, useWorkspace } from "@/lib/workspace";

// The API has no aggregate endpoint, so each count is the `total` of a one-item page.
function useCount(key: unknown[], fetchTotal: () => Promise<{ total: number }>) {
  return useQuery({ queryKey: key, queryFn: async () => (await fetchTotal()).total });
}

export function DashboardView() {
  const workspace = useWorkspace();
  const canCreate = useCan("MEMBER");
  const id = workspace.id;

  const statusCounts = useQueries({
    queries: WORKFLOW_STATUSES.map((status) => ({
      queryKey: ["workflows", id, "count", status],
      queryFn: async () => (await api.workflows.list(id, { status, limit: 1 })).total,
    })),
  });
  const activeAgents = useCount(["agents", id, "count", "ACTIVE"], () =>
    api.agents.list(id, { status: "ACTIVE", limit: 1 }),
  );
  const allAgents = useCount(["agents", id, "count", "all"], () => api.agents.list(id, { limit: 1 }));
  const pendingProposals = useCount(["proposals", id, "count", "PENDING"], () =>
    api.proposals.list(id, { status: "PENDING", limit: 1 }),
  );
  const recent = useQuery({
    queryKey: ["workflows", id, "list", { limit: 5, offset: 0 }],
    queryFn: () => api.workflows.list(id, { limit: 5 }),
  });

  const counts = new Map<WorkflowStatus, number | undefined>(
    WORKFLOW_STATUSES.map((s, i) => [s, statusCounts[i]?.data]),
  );
  const sum = (statuses: WorkflowStatus[]) =>
    statuses.every((s) => counts.get(s) !== undefined)
      ? statuses.reduce((n, s) => n + (counts.get(s) ?? 0), 0)
      : undefined;
  const countsFailed = statusCounts.find((q) => q.isError);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        description={`Overview of ${workspace.name}.`}
        actions={
          canCreate ? (
            <Button asChild>
              <Link href={workspacePath(id, "workflows/new")}>
                <PlusIcon data-icon="inline-start" />
                New workflow
              </Link>
            </Button>
          ) : null
        }
      />

      {countsFailed ? (
        <ErrorState error={countsFailed.error} onRetry={() => statusCounts.forEach((q) => void q.refetch())} />
      ) : (
        <section aria-labelledby="totals-heading" className="space-y-3">
          <h2 id="totals-heading" className="sr-only">
            Totals
          </h2>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Metric label="Running" value={sum(["RUNNING"])} />
            <Metric label="Needs attention" value={sum(["AWAITING_APPROVAL", "BLOCKED", "PAUSED"])} hint="Awaiting approval, blocked or paused" />
            <Metric label="In planning" value={sum(["PENDING", "PLANNING", "READY"])} />
            <Metric label="Finished" value={sum(["COMPLETED", "FAILED", "CANCELLED"])} hint="Completed, failed or cancelled" />
            <Metric label="Active agents" value={activeAgents.data} hint={allAgents.data !== undefined ? `${allAgents.data} registered, including built-ins` : undefined} />
            <Metric label="Pending proposals" value={pendingProposals.data} />
          </div>
        </section>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>
              <h2>Recent workflows</h2>
            </CardTitle>
            <CardDescription>Newest first.</CardDescription>
          </CardHeader>
          <CardContent>
            {recent.isPending ? (
              <ListSkeleton rows={3} label="Loading recent workflows" />
            ) : recent.isError ? (
              <ErrorState error={recent.error} onRetry={() => void recent.refetch()} />
            ) : recent.data.items.length === 0 ? (
              <p className="text-sm text-muted-foreground">No workflows yet.</p>
            ) : (
              <ul className="divide-y">
                {recent.data.items.map((w) => (
                  <li key={w.id} className="flex items-center gap-3 py-3">
                    <div className="min-w-0 flex-1">
                      <Link
                        href={workspacePath(id, `workflows/${w.id}`)}
                        className="line-clamp-1 font-medium hover:underline"
                      >
                        {w.prompt}
                      </Link>
                      <p className="text-xs text-muted-foreground">
                        Created <RelativeTime value={w.created_at} /> · Budget{" "}
                        {formatMoney(w.budget_limit, w.currency)}
                      </p>
                    </div>
                    <StatusBadge status={w.status} />
                  </li>
                ))}
              </ul>
            )}
            <Button variant="link" className="mt-2 px-0" asChild>
              <Link href={workspacePath(id, "workflows")}>
                All workflows <ArrowRightIcon data-icon="inline-end" />
              </Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>
              <h2>Workflows by status</h2>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-2 text-sm">
              {WORKFLOW_STATUSES.map((status) => (
                <div key={status} className="contents">
                  <dt className="text-muted-foreground">{humanize(status)}</dt>
                  <dd className="text-right font-medium tabular-nums">
                    {counts.get(status) ?? <Skeleton className="ml-auto h-4 w-6" />}
                  </dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>
      </div>

      <p className="text-xs text-muted-foreground">
        Spend, token usage and success rates are not shown: the platform does not measure them yet
        (model gateway and usage records arrive in Phase 2).
      </p>
    </div>
  );
}

function Metric({ label, value, hint }: { label: string; value: number | undefined; hint?: string }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl tabular-nums">
          {value === undefined ? <Skeleton className="h-7 w-10" /> : value}
        </CardTitle>
        {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      </CardHeader>
    </Card>
  );
}
