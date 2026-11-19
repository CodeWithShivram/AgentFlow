"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRightIcon } from "lucide-react";
import Link from "next/link";

import { ErrorState, ListSkeleton } from "@/components/common/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatRate } from "@/features/analytics/reliability";
import { attemptSuccessRate } from "@/features/analytics/usage-series";
import { api } from "@/lib/api";
import { formatCost } from "@/lib/format";
import { workspacePath } from "@/lib/navigation";
import { useWorkspace } from "@/lib/workspace";

const DAYS = 30;

function last30Days(): { from: string; to: string } {
  const to = new Date();
  return { from: new Date(to.getTime() - DAYS * 86_400_000).toISOString(), to: to.toISOString() };
}

/**
 * The dashboard's usage summary for the last 30 days (ADR-0045), from the analytics endpoints:
 * model calls, tokens, costs by basis (never added together) and the attempt success rate.
 */
export function UsageSummary() {
  const workspace = useWorkspace();
  const overview = useQuery({
    queryKey: ["analytics", workspace.id, "overview", DAYS],
    queryFn: () => api.analytics.overview(workspace.id, last30Days()),
  });
  const metrics = useQuery({
    queryKey: ["analytics", workspace.id, "metrics", DAYS],
    queryFn: () => api.analytics.metrics(workspace.id, last30Days()),
  });

  let body: React.ReactNode;
  if (overview.isPending || metrics.isPending) body = <ListSkeleton rows={3} label="Loading usage" />;
  else if (overview.isError) body = <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />;
  else if (metrics.isError) body = <ErrorState error={metrics.error} onRetry={() => void metrics.refetch()} />;
  else {
    const totals = overview.data.totals;
    const attempts = metrics.data.attempts;
    const rows: [string, string][] =
      totals.calls === 0
        ? [["Model calls", "No model calls yet"]]
        : [
            ["Model calls", String(totals.calls)],
            ["Tokens", `${totals.input_tokens.toLocaleString()} in · ${totals.output_tokens.toLocaleString()} out`],
            ...totals.costs.flatMap((c): [string, string][] => [
              [`Actual (${c.currency})`, formatCost(c.actual, c.currency)],
              [`Estimated (${c.currency})`, formatCost(c.estimated, c.currency)],
              [`Mock (${c.currency}), not a real charge`, formatCost(c.mock, c.currency)],
            ]),
            ...(totals.unpriced_calls > 0 ? [["Unpriced calls", String(totals.unpriced_calls)] as [string, string]] : []),
          ];
    rows.push(["Agent attempts succeeded", formatRate(attemptSuccessRate(attempts.by_status, attempts.failure_rate))]);
    body = (
      <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-2 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="text-right font-medium tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    );
  }

  return (
    <Card role="region" aria-label="Usage, last 30 days">
      <CardHeader>
        <CardTitle>
          <h2>Usage, last 30 days</h2>
        </CardTitle>
        <CardDescription>From the stored usage records.</CardDescription>
      </CardHeader>
      <CardContent>
        {body}
        <Button variant="link" className="mt-2 px-0" asChild>
          <Link href={workspacePath(workspace.id, "analytics")}>
            Analytics <ArrowRightIcon data-icon="inline-end" />
          </Link>
        </Button>
      </CardContent>
    </Card>
  );
}
