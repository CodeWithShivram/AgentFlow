"use client";

import { useState } from "react";

import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { Agent, Task } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { ExecutionsPanel } from "./executions-panel";
import { criticalPath, downstream, layoutGraph, upstream } from "./graph";

// Fixed geometry, matched by the card and column classes below, so the edges can be drawn
// without measuring the DOM.
const COL_W = 240;
const COL_GAP = 64;
const HEADER_H = 32;
const CARD_H = 136;
const ROW_GAP = 16;

const x = (stage: number) => stage * (COL_W + COL_GAP);
const y = (row: number) => HEADER_H + row * (CARD_H + ROW_GAP);

/**
 * The workflow's task DAG (ADR-0044): tasks in stages, every dependency drawn as an arrow.
 * Selecting a task highlights everything it waits on and everything that waits on it, and opens
 * its details. The "After: …" text on each card carries the same information as the arrows, so
 * nothing depends on seeing them. Statuses refresh with the workflow page.
 */
export function DependencyGraph({ tasks, agentsById }: { tasks: Task[]; agentsById: Map<string, Agent> }) {
  const visible = tasks.filter((t) => !t.removed);
  const [focused, setFocused] = useState<string | null>(null);
  const [showCritical, setShowCritical] = useState(false);

  if (visible.length === 0) return <p className="text-sm text-muted-foreground">No tasks yet.</p>;

  const layout = layoutGraph(visible);
  const titles = new Map(visible.map((t) => [t.id, t.title]));
  const position = new Map(layout.nodes.map((n) => [n.task.id, n]));
  const focusTask = visible.find((t) => t.id === focused) ?? null;
  const before = focusTask ? upstream(visible, focusTask.id) : new Set<string>();
  const after = focusTask ? downstream(visible, focusTask.id) : new Set<string>();
  const chain = new Set<string>([...before, ...after, ...(focusTask ? [focusTask.id] : [])]);
  const critical = showCritical ? criticalPath(visible) : [];
  const onCritical = new Set(critical);
  const criticalEdges = new Set(critical.slice(1).map((id, i) => `${critical[i]}>${id}`));
  const rows = Math.max(...layout.stages.map((s) => s.length));
  const width = x(layout.stages.length - 1) + COL_W;
  const height = y(rows - 1) + CARD_H;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2">
          <Switch id="critical-path" checked={showCritical} onCheckedChange={setShowCritical} />
          <Label htmlFor="critical-path">Show critical path</Label>
        </div>
        {showCritical ? (
          <p className="text-sm text-muted-foreground" role="status">
            {critical.length === 0
              ? "Every required task is finished."
              : `Critical path: ${critical.map((id) => titles.get(id)).join(" → ")}`}
          </p>
        ) : null}
      </div>

      <div className="overflow-x-auto pb-2">
        <div className="relative" style={{ width, height }}>
          <svg className="pointer-events-none absolute inset-0" width={width} height={height} aria-hidden>
            <defs>
              <marker id="dag-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" orient="auto">
                <path d="M0,0 L8,4 L0,8 z" className="fill-muted-foreground" />
              </marker>
              <marker id="dag-arrow-on" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" orient="auto">
                <path d="M0,0 L8,4 L0,8 z" className="fill-primary" />
              </marker>
            </defs>
            {layout.edges.map(({ from, to }) => {
              const a = position.get(from);
              const b = position.get(to);
              if (!a || !b) return null;
              const x1 = x(a.stage) + COL_W;
              const y1 = y(a.row) + CARD_H / 2;
              const x2 = x(b.stage) - 2;
              const y2 = y(b.row) + CARD_H / 2;
              const bend = Math.max(24, (x2 - x1) / 2);
              const on = (chain.has(from) && chain.has(to)) || criticalEdges.has(`${from}>${to}`);
              return (
                <path
                  key={`${from}>${to}`}
                  data-edge={`${from}>${to}`}
                  d={`M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}`}
                  fill="none"
                  strokeWidth={on ? 2 : 1.25}
                  className={cn(on ? "stroke-primary" : "stroke-muted-foreground/60", focusTask && !on && "opacity-30")}
                  markerEnd={on ? "url(#dag-arrow-on)" : "url(#dag-arrow)"}
                />
              );
            })}
          </svg>

          <ol className="contents" aria-label="Task stages">
            {layout.stages.map((stage, index) => (
              <li key={index} className="contents">
                <section
                  aria-label={`Stage ${index + 1}`}
                  className="absolute top-0"
                  style={{ left: x(index), width: COL_W }}
                >
                  <h3 className="h-8 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                    Stage {index + 1}
                  </h3>
                  <ul>
                    {stage.map((task, row) => {
                      const agent = task.assigned_agent_id ? agentsById.get(task.assigned_agent_id) : undefined;
                      return (
                        <li key={task.id} className="absolute w-full" style={{ top: y(row), height: CARD_H }}>
                          <button
                            type="button"
                            aria-pressed={focused === task.id}
                            onClick={() => setFocused(focused === task.id ? null : task.id)}
                            className={cn(
                              "flex h-full w-full flex-col gap-1 overflow-hidden rounded-md border bg-card p-3 text-left text-sm transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                              focused === task.id && "border-primary ring-1 ring-primary",
                              focusTask && focused !== task.id && chain.has(task.id) && "border-primary/60 bg-muted",
                              focusTask && !chain.has(task.id) && "opacity-50",
                              onCritical.has(task.id) && "border-amber-500 ring-1 ring-amber-500",
                            )}
                          >
                            <span className="line-clamp-2 font-medium">{task.title}</span>
                            <span className="flex flex-wrap items-center gap-1">
                              <StatusBadge status={task.status} />
                              {!task.required ? <span className="text-xs text-muted-foreground">Optional</span> : null}
                              {onCritical.has(task.id) ? (
                                <span className="text-xs font-medium text-amber-700 dark:text-amber-300">Critical path</span>
                              ) : null}
                            </span>
                            <span className="truncate text-xs text-muted-foreground">
                              {agent ? agent.name : task.assigned_agent_id ? "Assigned agent" : "Unassigned"} · attempts{" "}
                              {task.attempt_count}
                              {task.priority !== 0 ? ` · priority ${task.priority}` : ""}
                            </span>
                            {task.depends_on.length > 0 ? (
                              <span className="truncate text-xs text-muted-foreground">
                                After: {task.depends_on.map((id) => titles.get(id) ?? "removed task").join(", ")}
                              </span>
                            ) : null}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              </li>
            ))}
          </ol>
        </div>
      </div>

      {focusTask ? (
        <TaskDetails
          task={focusTask}
          titles={titles}
          before={before}
          after={after}
          agentsById={agentsById}
          onClose={() => setFocused(null)}
        />
      ) : (
        <p className="text-sm text-muted-foreground">Select a task to see what it waits on, what waits on it, and its attempts.</p>
      )}
    </div>
  );
}

function TaskDetails({
  task,
  titles,
  before,
  after,
  agentsById,
  onClose,
}: {
  task: Task;
  titles: Map<string, string>;
  before: Set<string>;
  after: Set<string>;
  agentsById: Map<string, Agent>;
  onClose: () => void;
}) {
  const names = (ids: Iterable<string>) =>
    [...ids].map((id) => titles.get(id) ?? "removed task").join(", ") || "Nothing";
  const agent = task.assigned_agent_id ? agentsById.get(task.assigned_agent_id) : undefined;
  const rows: [string, string][] = [
    ["Status", humanize(task.status)],
    ["Required", task.required ? "Yes" : "No (optional)"],
    ["Priority", String(task.priority)],
    ["Agent", agent ? agent.name : task.assigned_agent_id ? "Assigned agent" : "Unassigned"],
    ["Skills", task.required_skills.map(humanize).join(", ") || "None"],
    ["Waits on directly", names(task.depends_on)],
    ["Waits on (all)", names(before)],
    ["Waited on by (all)", names(after)],
  ];
  return (
    <Card role="region" aria-label="Task details">
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <CardTitle>
          <h3 className="break-words">{task.title}</h3>
        </CardTitle>
        <Button variant="outline" size="sm" onClick={onClose}>
          Close
        </Button>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-[max-content_1fr]">
          {rows.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="font-medium break-words">{value}</dd>
            </div>
          ))}
        </dl>
        <ExecutionsPanel task={task} agentsById={agentsById} />
      </CardContent>
    </Card>
  );
}
