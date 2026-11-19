"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { ErrorState, ListSkeleton } from "@/components/common/states";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { Distribution, MetricRate } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";

/** "25% (1 of 4)"; "Not measured" when nothing was measured, never 0%. */
export function formatRate(rate: MetricRate): string {
  if (rate.value === null) return "Not measured";
  const percent = rate.value * 100;
  const shown = Number.isInteger(percent) ? String(percent) : percent.toFixed(1);
  return `${shown}% (${rate.numerator} of ${rate.denominator})`;
}

function amount(value: number, unit: Distribution["unit"]): string {
  if (unit === "ms") return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`;
  return value >= 120 ? `${(value / 60).toFixed(1)} min` : `${value.toFixed(1)} s`;
}

/** "median 250 ms · 95th 385 ms · max 400 ms (4)"; "Not measured" without samples. */
export function formatDistribution(d: Distribution): string {
  if (d.samples === 0 || d.p50 === null || d.p95 === null || d.max === null) return "Not measured";
  return `median ${amount(d.p50, d.unit)} · 95th ${amount(d.p95, d.unit)} · max ${amount(d.max, d.unit)} (${d.samples})`;
}

function Facts({ rows }: { rows: [string, string][] }) {
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

function counts(byStatus: Record<string, number>): string {
  const entries = Object.entries(byStatus);
  return entries.length === 0 ? "None" : entries.map(([s, n]) => `${n} ${humanize(s).toLowerCase()}`).join(", ");
}

/**
 * Latency, retry and failure metrics (ADR-0043), from stored records. Each measurement counts in
 * the period it finished; running work is "in flight" and kept out of durations and rates.
 */
export function ReliabilitySection({ days, range }: { days: number; range: () => { from: string; to: string } }) {
  const workspace = useWorkspace();
  const query = useQuery({
    queryKey: ["analytics", workspace.id, "metrics", days],
    queryFn: () => api.analytics.metrics(workspace.id, range()),
    placeholderData: keepPreviousData,
  });
  return (
    <Card role="region" aria-label="Reliability and latency">
      <CardHeader>
        <CardTitle>
          <h2>Reliability and latency</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-6 text-sm">
        {query.isPending ? (
          <ListSkeleton label="Loading reliability and latency" />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : (
          (() => {
            const m = query.data;
            return (
              <>
                <section className="space-y-2" aria-label="Agent attempts">
                  <h3 className="font-medium">Agent attempts</h3>
                  <Facts
                    rows={[
                      ["Finished", `${m.attempts.finished} (${counts(m.attempts.by_status)})`],
                      ["In flight", String(m.attempts.in_flight)],
                      ["Failure rate (cancelled excluded)", formatRate(m.attempts.failure_rate)],
                      ["Retry rate", formatRate(m.attempts.retry_rate)],
                      ["Duration", formatDistribution(m.attempts.duration)],
                      ["Tasks accepted on the first attempt", formatRate(m.tasks.first_attempt)],
                      ["Deterministic checks passed", formatRate(m.verification.deterministic_pass_rate)],
                      ["Semantic evaluations passed", formatRate(m.verification.semantic_pass_rate)],
                    ]}
                  />
                  {m.attempts.by_agent.length > 0 ? (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Agent</TableHead>
                          <TableHead className="text-right">Attempts</TableHead>
                          <TableHead className="text-right">Failure rate</TableHead>
                          <TableHead className="text-right">Retry rate</TableHead>
                          <TableHead className="text-right">Duration</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {m.attempts.by_agent.map((g) => (
                          <TableRow key={g.key}>
                            <TableCell>{g.label}</TableCell>
                            <TableCell className="text-right">{g.attempts}</TableCell>
                            <TableCell className="text-right">{formatRate(g.failure_rate)}</TableCell>
                            <TableCell className="text-right">{formatRate(g.retry_rate)}</TableCell>
                            <TableCell className="text-right">{formatDistribution(g.duration)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  ) : null}
                </section>

                <section className="space-y-2" aria-label="Model calls">
                  <h3 className="font-medium">Model calls</h3>
                  <Facts
                    rows={[
                      ["Calls", `${m.model_calls.calls} (${counts(m.model_calls.by_status)})`],
                      ["Failure rate", formatRate(m.model_calls.failure_rate)],
                      ["Latency", formatDistribution(m.model_calls.latency)],
                    ]}
                  />
                  {m.model_calls.by_model.length > 0 ? (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Model</TableHead>
                          <TableHead className="text-right">Calls</TableHead>
                          <TableHead className="text-right">Failure rate</TableHead>
                          <TableHead className="text-right">Latency</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {m.model_calls.by_model.map((g) => (
                          <TableRow key={g.key}>
                            <TableCell>{g.label}</TableCell>
                            <TableCell className="text-right">{g.calls}</TableCell>
                            <TableCell className="text-right">{formatRate(g.failure_rate)}</TableCell>
                            <TableCell className="text-right">{formatDistribution(g.latency)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  ) : null}
                </section>

                <section className="space-y-2" aria-label="Tool calls">
                  <h3 className="font-medium">Tool calls</h3>
                  <Facts
                    rows={[
                      ["Calls", `${m.tools.calls} (${counts(m.tools.by_status)})`],
                      ["Failure rate", formatRate(m.tools.failure_rate)],
                      ["Denied by policy", formatRate(m.tools.denial_rate)],
                      ["Duration", formatDistribution(m.tools.duration)],
                    ]}
                  />
                </section>

                <section className="space-y-2" aria-label="Workflows">
                  <h3 className="font-medium">Workflows</h3>
                  <Facts
                    rows={[
                      ["Finished", `${m.workflows.finished} (${counts(m.workflows.by_status)})`],
                      ["In flight", String(m.workflows.in_flight)],
                      ["Run time", formatDistribution(m.workflows.duration)],
                    ]}
                  />
                </section>
              </>
            );
          })()
        )}
      </CardContent>
    </Card>
  );
}
