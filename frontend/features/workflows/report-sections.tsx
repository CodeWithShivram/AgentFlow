"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { ErrorState, ListSkeleton } from "@/components/common/states";
import { StatusBadge } from "@/components/common/status-badge";
import { api } from "@/lib/api";
import type { ReportTask, TaskStatus, WorkflowEvent } from "@/lib/api/types";
import { formatDateTime, humanize } from "@/lib/format";
import { workspacePath } from "@/lib/navigation";

import { COL_W, GraphEdges, HEADER_H, ROW_GAP, x } from "./dependency-graph";
import { CATEGORY_LABELS, describeEvent, eventCategory } from "./event-timeline";
import { type GraphTask, layoutGraph } from "./graph";
import { workflowKeys } from "./use-workflow";

const CARD_H = 96;
/** The events API returns at most this many per request (ADR-0045). */
export const REPORT_EVENT_LIMIT = 500;

type ReportGraphTask = GraphTask & { agent: string | null; attempts: number };

/**
 * The report's dependency graph (ADR-0045): the task graph of ADR-0044, static and printable.
 * Arrows are decorative; each card's "After: …" line carries the same facts.
 */
export function ReportDependencyGraph({ tasks }: { tasks: ReportTask[] }) {
  const visible: ReportGraphTask[] = tasks
    .filter((t) => !t.removed)
    .map((t) => ({
      id: t.id,
      title: t.title,
      depends_on: t.depends_on,
      priority: t.priority,
      required: t.required,
      removed: t.removed,
      status: t.status as TaskStatus,
      agent: t.assigned_agent_name,
      attempts: t.attempts.length,
    }));
  if (visible.length === 0) return <p className="text-muted-foreground">No tasks.</p>;
  const layout = layoutGraph(visible);
  const titles = new Map(visible.map((t) => [t.id, t.title]));
  const rows = Math.max(...layout.stages.map((s) => s.length));
  const rowY = (row: number) => HEADER_H + row * (CARD_H + ROW_GAP);
  const width = x(layout.stages.length - 1) + COL_W;
  const height = rowY(rows - 1) + CARD_H;
  return (
    <div className="overflow-x-auto pb-2 print:overflow-visible">
      <div className="relative" style={{ width, height }}>
        <GraphEdges layout={layout} cardHeight={CARD_H} />
        <ol className="contents" aria-label="Report task stages">
          {layout.stages.map((stage, index) => (
            <li key={index} className="contents">
              <section aria-label={`Report stage ${index + 1}`} className="absolute top-0" style={{ left: x(index), width: COL_W }}>
                <h3 className="h-8 text-xs font-medium tracking-wide text-muted-foreground uppercase">Stage {index + 1}</h3>
                <ul>
                  {stage.map((task, row) => (
                    <li
                      key={task.id}
                      className="absolute flex w-full flex-col gap-1 overflow-hidden rounded-md border bg-card p-2 text-xs"
                      style={{ top: rowY(row), height: CARD_H }}
                    >
                      <span className="line-clamp-1 text-sm font-medium">{task.title}</span>
                      <span className="flex flex-wrap items-center gap-1">
                        <StatusBadge status={task.status} />
                        {!task.required ? <span className="text-muted-foreground">Optional</span> : null}
                      </span>
                      <span className="truncate text-muted-foreground">
                        {task.agent ?? "Unassigned"} · attempts {task.attempts}
                      </span>
                      {task.depends_on.length > 0 ? (
                        <span className="truncate text-muted-foreground">
                          After: {task.depends_on.map((id) => titles.get(id) ?? "a removed task").join(", ")}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </section>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

/** The event without people's free text (ADR-0042: the report carries none). */
function withoutFreeText(event: WorkflowEvent): WorkflowEvent {
  return { ...event, payload: Object.fromEntries(Object.entries(event.payload).filter(([key]) => key !== "reason")) };
}

/**
 * The report's execution timeline (ADR-0045): the workflow's events in order, from the existing
 * events endpoint, at most one page; the workflow's Timeline tab has the full, filterable history.
 */
export function ReportTimeline({
  workspaceId,
  workflowId,
  titles,
}: {
  workspaceId: string;
  workflowId: string;
  titles: Map<string, string>;
}) {
  const events = useQuery({
    queryKey: [...workflowKeys.detail(workspaceId, workflowId), "report-events"],
    queryFn: () => api.workflows.events(workspaceId, workflowId, { after: 0, limit: REPORT_EVENT_LIMIT }),
  });
  if (events.isPending) return <ListSkeleton rows={3} label="Loading the timeline" />;
  if (events.isError) return <ErrorState error={events.error} onRetry={() => void events.refetch()} />;
  const items = events.data.items;
  if (items.length === 0) return <p className="text-muted-foreground">No events yet.</p>;
  return (
    <>
      <ol className="space-y-2" aria-label="Workflow events, oldest first">
        {items.map((event) => (
          <li key={event.id} className="grid gap-x-4 sm:grid-cols-[11rem_1fr]">
            <span className="text-xs text-muted-foreground tabular-nums">{formatDateTime(event.created_at)}</span>
            <span className="break-words">
              <span className="font-medium">{humanize(event.event_type)}</span>
              <span className="text-muted-foreground">
                {" "}
                · {describeEvent(withoutFreeText(event), event.task_id ? titles.get(event.task_id) : undefined)} ·{" "}
                {CATEGORY_LABELS[eventCategory(event.event_type)]}
              </span>
            </span>
          </li>
        ))}
      </ol>
      {items.length >= REPORT_EVENT_LIMIT ? (
        <p className="text-xs text-muted-foreground">
          Showing the first {REPORT_EVENT_LIMIT} events.{" "}
          <Link className="underline print:no-underline" href={workspacePath(workspaceId, `workflows/${workflowId}`)}>
            The workflow&apos;s Timeline
          </Link>{" "}
          has the full history.
        </p>
      ) : null}
    </>
  );
}
