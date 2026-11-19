"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { ErrorState, ListSkeleton } from "@/components/common/states";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useWorkspace } from "@/lib/workspace";

import { type UsageBucket, daysBetween, localDay, niceMax, usageBuckets, viewerTimeZone } from "./usage-series";

const CHART_H = 160;

function dayLabel(day: string): string {
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", timeZone: "UTC" }).format(
    new Date(`${day}T00:00:00Z`),
  );
}

function bucketLabel(b: UsageBucket): string {
  return b.start === b.end ? dayLabel(b.start) : `${dayLabel(b.start)} – ${dayLabel(b.end)}`;
}

function describe(b: UsageBucket): string {
  const tokens = b.inputTokens + b.outputTokens;
  return `${bucketLabel(b)}: ${tokens.toLocaleString()} tokens (${b.inputTokens.toLocaleString()} in · ${b.outputTokens.toLocaleString()} out) · ${b.calls} ${b.calls === 1 ? "call" : "calls"}`;
}

/**
 * Tokens per day (per week past a month) in the viewer's time zone (ADR-0045), from the stored
 * usage records. One measure on one axis; calls are in the readout and the table. Costs stay in
 * the tables, since actual, estimated and mock amounts are never added together.
 */
export function UsageOverTime({ days, range }: { days: number; range: () => { from: string; to: string } }) {
  const workspace = useWorkspace();
  const timeZone = viewerTimeZone();
  const query = useQuery({
    queryKey: ["analytics", workspace.id, "tokens-by-day", days, timeZone],
    queryFn: () => api.analytics.tokens(workspace.id, { group_by: "day", tz: timeZone, limit: 400, ...range() }),
    placeholderData: keepPreviousData,
  });
  const [active, setActive] = useState<number | null>(null);

  let body: React.ReactNode;
  if (query.isPending) body = <ListSkeleton label="Loading usage over time" />;
  else if (query.isError) body = <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  else if (query.data.totals.calls === 0) body = <p className="text-muted-foreground">No model calls in this range.</p>;
  else {
    const data = query.data;
    const zone = data.time_zone;
    const buckets = usageBuckets(
      data.rows,
      daysBetween(localDay(new Date(data.range.start), zone), localDay(new Date(data.range.end), zone)),
    );
    const weekly = buckets.some((b) => b.start !== b.end);
    const top = niceMax(Math.max(...buckets.map((b) => b.inputTokens + b.outputTokens)));
    const current = active !== null ? buckets[active] : undefined;
    const total = data.totals.input_tokens + data.totals.output_tokens;

    const onKeyDown = (event: React.KeyboardEvent) => {
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        event.preventDefault();
        const step = event.key === "ArrowRight" ? 1 : -1;
        setActive((i) => (i === null ? (step > 0 ? 0 : buckets.length - 1) : Math.min(buckets.length - 1, Math.max(0, i + step))));
      } else if (event.key === "Escape") setActive(null);
    };

    body = (
      <div className="space-y-3">
        <p className="min-h-5 text-sm" aria-live="polite">
          {current
            ? describe(current)
            : `${total.toLocaleString()} tokens · ${data.totals.calls} calls in this range. Point at or select a ${weekly ? "week" : "day"} for its numbers.`}
        </p>
        <div className="flex gap-2">
          <div className="flex flex-col justify-between text-right text-xs text-muted-foreground tabular-nums" style={{ height: CHART_H }} aria-hidden>
            <span>{top.toLocaleString()}</span>
            <span>{(top / 2).toLocaleString()}</span>
            <span>0</span>
          </div>
          <div
            role="group"
            aria-label={`Tokens per ${weekly ? "week" : "day"}. Use the arrow keys to read each ${weekly ? "week" : "day"}.`}
            tabIndex={0}
            onKeyDown={onKeyDown}
            onBlur={() => setActive(null)}
            onPointerLeave={() => setActive(null)}
            className="relative min-w-0 flex-1 rounded-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            style={{ height: CHART_H }}
          >
            {[0, 0.5, 1].map((f) => (
              <div key={f} className="absolute inset-x-0 border-t border-border" style={{ bottom: f * CHART_H }} aria-hidden />
            ))}
            <div className="absolute inset-0 flex items-end gap-0.5" aria-hidden>
              {buckets.map((b, i) => {
                const tokens = b.inputTokens + b.outputTokens;
                return (
                  <div
                    key={b.start}
                    data-bucket={b.start}
                    className="flex h-full min-w-0 flex-1 items-end justify-center"
                    onPointerEnter={() => setActive(i)}
                  >
                    <div
                      className={cn("w-full max-w-6 rounded-t-[4px] bg-primary", active === i && "bg-primary/70")}
                      style={{ height: tokens === 0 ? 0 : Math.max(2, (tokens / top) * CHART_H) }}
                    />
                  </div>
                );
              })}
            </div>
          </div>
        </div>
        <div className="flex justify-between pl-10 text-xs text-muted-foreground" aria-hidden>
          <span>{dayLabel(buckets[0]!.start)}</span>
          <span>{dayLabel(buckets[buckets.length - 1]!.end)}</span>
        </div>
        <p className="text-xs text-muted-foreground">
          Days in {zone}. Tokens in and out; costs are in the tables below.
        </p>
        <details>
          <summary className="cursor-pointer text-sm">Show as table</summary>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{weekly ? "Week" : "Day"}</TableHead>
                <TableHead className="text-right">Calls</TableHead>
                <TableHead className="text-right">Tokens in</TableHead>
                <TableHead className="text-right">Tokens out</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {buckets.map((b) => (
                <TableRow key={b.start}>
                  <TableCell>{bucketLabel(b)}</TableCell>
                  <TableCell className="text-right">{b.calls}</TableCell>
                  <TableCell className="text-right">{b.inputTokens.toLocaleString()}</TableCell>
                  <TableCell className="text-right">{b.outputTokens.toLocaleString()}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </details>
      </div>
    );
  }

  return (
    <Card role="region" aria-label="Usage over time">
      <CardHeader>
        <CardTitle>
          <h2>Usage over time</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="text-sm">{body}</CardContent>
    </Card>
  );
}
