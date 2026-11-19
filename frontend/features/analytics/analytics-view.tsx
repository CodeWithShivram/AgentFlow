"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { PageHeader } from "@/components/common/page-header";
import { ErrorState, ListSkeleton } from "@/components/common/states";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import type { AnalyticsGroupBy, BreakdownRow, CostByCurrency, UsageTotals } from "@/lib/api/types";
import { formatCost } from "@/lib/format";
import { workspacePath } from "@/lib/navigation";
import { useWorkspace } from "@/lib/workspace";

import { ReliabilitySection } from "./reliability";

const RANGES = [7, 30, 90, 365] as const;
type Days = (typeof RANGES)[number];

export const analyticsKeys = {
  all: (workspaceId: string) => ["analytics", workspaceId] as const,
};

/** The window ends now; computed when the request is sent, so a cached page is never stale. */
function rangeFor(days: Days): { from: string; to: string } {
  const to = new Date();
  return { from: new Date(to.getTime() - days * 86_400_000).toISOString(), to: to.toISOString() };
}

/**
 * Model calls, tokens and costs of the workspace (ADR-0041), by workflow, agent and model.
 * Actual, estimated and mock costs are shown apart, never added together (ADR-0040).
 */
export function AnalyticsView() {
  const workspace = useWorkspace();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const raw = Number(params.get("days"));
  const days: Days = (RANGES as readonly number[]).includes(raw) ? (raw as Days) : 30;

  const overview = useQuery({
    queryKey: [...analyticsKeys.all(workspace.id), "overview", days],
    queryFn: () => api.analytics.overview(workspace.id, rangeFor(days)),
    placeholderData: keepPreviousData,
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Analytics"
        description="Model calls, tokens and costs, from the stored usage records. Actual, estimated and mock costs are shown apart."
      />
      <Tabs
        value={String(days)}
        onValueChange={(v) => router.replace(v === "30" ? pathname : `${pathname}?days=${v}`, { scroll: false })}
      >
        <TabsList aria-label="Time range">
          {RANGES.map((d) => (
            <TabsTrigger key={d} value={String(d)}>
              {d === 365 ? "Last year" : `Last ${d} days`}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <Card role="region" aria-label="Overview">
        <CardHeader>
          <CardTitle>
            <h2>Overview</h2>
          </CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          {overview.isPending ? (
            <ListSkeleton label="Loading overview" />
          ) : overview.isError ? (
            <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />
          ) : (
            <Totals
              totals={overview.data.totals}
              extra={[
                ["Workflows with model calls", String(overview.data.workflows)],
                ["Agents with model calls", String(overview.data.agents)],
              ]}
            />
          )}
        </CardContent>
      </Card>
      <ReliabilitySection days={days} range={() => rangeFor(days)} />
      <Breakdown title="By workflow" groupBy="workflow" days={days} />
      <Breakdown title="By agent" groupBy="agent" days={days} />
      <Breakdown title="By model" groupBy="model" days={days} />
    </div>
  );
}

function costLines(costs: CostByCurrency[]): [string, string][] {
  return costs.flatMap((c) => [
    [`Actual (${c.currency})`, formatCost(c.actual, c.currency)],
    [`Estimated (${c.currency})`, formatCost(c.estimated, c.currency)],
    [`Mock provider (${c.currency}), not a real charge`, formatCost(c.mock, c.currency)],
  ]);
}

function Totals({ totals, extra }: { totals: UsageTotals; extra: [string, string][] }) {
  if (totals.calls === 0) return <p className="text-muted-foreground">No model calls in this range.</p>;
  const rows: [string, string][] = [
    ["Model calls", String(totals.calls)],
    ...extra,
    [
      "Tokens",
      `${totals.input_tokens.toLocaleString()} in · ${totals.output_tokens.toLocaleString()} out · ${totals.cached_input_tokens.toLocaleString()} cached`,
    ],
    ...costLines(totals.costs),
  ];
  if (totals.unpriced_calls > 0) rows.push(["Unpriced calls", String(totals.unpriced_calls)]);
  return (
    <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-[max-content_1fr]">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="font-medium">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function CostCell({ costs, basis }: { costs: CostByCurrency[]; basis: "actual" | "estimated" | "mock" }) {
  if (costs.length === 0) return <span className="text-muted-foreground">—</span>;
  return (
    <>
      {costs.map((c) => (
        <div key={c.currency}>{formatCost(c[basis], c.currency)}</div>
      ))}
    </>
  );
}

function Breakdown({ title, groupBy, days }: { title: string; groupBy: AnalyticsGroupBy; days: Days }) {
  const workspace = useWorkspace();
  const query = useQuery({
    queryKey: [...analyticsKeys.all(workspace.id), "costs", groupBy, days],
    queryFn: () => api.analytics.costs(workspace.id, { group_by: groupBy, ...rangeFor(days) }),
    placeholderData: keepPreviousData,
  });
  return (
    <Card role="region" aria-label={title}>
      <CardHeader>
        <CardTitle>
          <h2>{title}</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="text-sm">
        {query.isPending ? (
          <ListSkeleton label={`Loading ${title.toLowerCase()}`} />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : query.data.rows.length === 0 ? (
          <p className="text-muted-foreground">No model calls in this range.</p>
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead className="text-right">Calls</TableHead>
                  <TableHead className="text-right">Tokens in</TableHead>
                  <TableHead className="text-right">Tokens out</TableHead>
                  <TableHead className="text-right">Actual</TableHead>
                  <TableHead className="text-right">Estimated</TableHead>
                  <TableHead className="text-right">Mock (not charged)</TableHead>
                  <TableHead className="text-right">Unpriced calls</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {query.data.rows.map((row) => (
                  <TableRow key={row.key}>
                    <TableCell className="max-w-72 truncate">
                      <RowName row={row} groupBy={groupBy} workspaceId={workspace.id} />
                    </TableCell>
                    <TableCell className="text-right">{row.calls}</TableCell>
                    <TableCell className="text-right">{row.input_tokens.toLocaleString()}</TableCell>
                    <TableCell className="text-right">{row.output_tokens.toLocaleString()}</TableCell>
                    <TableCell className="text-right">
                      <CostCell costs={row.costs} basis="actual" />
                    </TableCell>
                    <TableCell className="text-right">
                      <CostCell costs={row.costs} basis="estimated" />
                    </TableCell>
                    <TableCell className="text-right">
                      <CostCell costs={row.costs} basis="mock" />
                    </TableCell>
                    <TableCell className="text-right">{row.unpriced_calls}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {query.data.truncated ? (
              <p className="mt-2 text-xs text-muted-foreground">Showing the groups with the most calls.</p>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function RowName({ row, groupBy, workspaceId }: { row: BreakdownRow; groupBy: AnalyticsGroupBy; workspaceId: string }) {
  if (groupBy === "workflow" && row.key !== "none") {
    return (
      <Link className="underline" href={workspacePath(workspaceId, `workflows/${row.key}`)}>
        {row.label}
      </Link>
    );
  }
  return <span>{row.label}</span>;
}
