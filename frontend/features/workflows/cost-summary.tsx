"use client";

import { useQuery } from "@tanstack/react-query";

import { ErrorState } from "@/components/common/states";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { Uuid, Workflow, WorkflowCosts } from "@/lib/api/types";
import { formatCost } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";

import { workflowKeys } from "./use-workflow";

export const costKeys = (workspaceId: Uuid, workflowId: Uuid) =>
  [...workflowKeys.detail(workspaceId, workflowId), "costs"] as const;

function useWorkflowCosts(workflowId: Uuid) {
  const workspace = useWorkspace();
  return useQuery({
    queryKey: costKeys(workspace.id, workflowId),
    queryFn: () => api.workflows.costs(workspace.id, workflowId),
  });
}

/** The "Budget spent" value (ADR-0040): actual plus estimated spend against the budget. */
export function BudgetSpent({ workflow }: { workflow: Workflow }) {
  const costs = useWorkflowCosts(workflow.id);
  if (costs.isPending) return <Skeleton className="h-4 w-32" />;
  if (costs.isError) return <span>Unavailable</span>;
  const budget = costs.data.budget;
  if (!budget) return <span>No budget set</span>;
  if (!budget.comparable) {
    const priced = costs.data.totals.map((t) => t.currency).join(", ");
    return (
      <span>
        Not comparable ({budget.currency} budget, prices in {priced}); not enforced
      </span>
    );
  }
  return (
    <span>
      {formatCost(budget.counted_spend, budget.currency)} of {formatCost(budget.limit, budget.currency)}
      {budget.reached ? " (reached)" : ""}
    </span>
  );
}

/**
 * The workflow's model costs (ADR-0040). Actual, estimated and mock costs are shown apart and
 * never added together as one charge; every figure comes from the stored usage records.
 */
export function CostSummary({ workflowId }: { workflowId: Uuid }) {
  const costs = useWorkflowCosts(workflowId);
  return (
    <Card role="region" aria-label="Model costs">
      <CardHeader>
        <CardTitle>
          <h2>Model costs</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="text-sm">
        {costs.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : costs.isError ? (
          <ErrorState error={costs.error} onRetry={() => void costs.refetch()} />
        ) : costs.data.calls === 0 ? (
          <p className="text-muted-foreground">No model calls yet.</p>
        ) : (
          <CostDetails costs={costs.data} />
        )}
      </CardContent>
    </Card>
  );
}

function CostDetails({ costs }: { costs: WorkflowCosts }) {
  const rows: [string, string][] = [];
  for (const t of costs.totals) {
    rows.push([`Actual (${t.currency})`, formatCost(t.actual, t.currency)]);
    rows.push([`Estimated (${t.currency})`, formatCost(t.estimated, t.currency)]);
    if (Number(t.mock) !== 0 || costs.totals.length === 1) {
      rows.push([`Mock provider (${t.currency}), not a real charge`, formatCost(t.mock, t.currency)]);
    }
  }
  const statuses = Object.entries(costs.calls_by_status)
    .map(([status, count]) => `${count} ${status.toLowerCase().replaceAll("_", " ")}`)
    .join(", ");
  rows.push(["Model calls", `${costs.calls} (${statuses})`]);
  if (costs.unpriced_calls > 0) {
    rows.push(["Unpriced calls", `${costs.unpriced_calls} without a price or reported usage`]);
  }
  rows.push([
    "Tokens",
    `${costs.input_tokens.toLocaleString()} in · ${costs.output_tokens.toLocaleString()} out · ${costs.cached_input_tokens.toLocaleString()} cached`,
  ]);
  return (
    <div className="space-y-3">
      <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-[max-content_1fr]">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="font-medium">{value}</dd>
          </div>
        ))}
      </dl>
      <p className={costs.reconciled ? "text-muted-foreground" : "font-medium text-destructive"}>
        {costs.reconciled
          ? `Reconciled: all ${costs.checked_calls} priced calls match their price snapshot.`
          : `Not reconciled: ${costs.mismatched_calls} of ${costs.checked_calls} priced calls do not match their price snapshot.`}
      </p>
    </div>
  );
}
