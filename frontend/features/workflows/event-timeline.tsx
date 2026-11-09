"use client";

import { RelativeTime } from "@/components/common/relative-time";
import type { Task, WorkflowEvent } from "@/lib/api/types";
import { humanize } from "@/lib/format";

/** A one-line, plain-text summary of an event payload. Values are never rendered as HTML. */
export function describeEvent(event: WorkflowEvent, taskTitle?: string): string {
  const p = event.payload;
  const parts: string[] = [];
  const text = (key: string) => (typeof p[key] === "string" || typeof p[key] === "number" ? String(p[key]) : null);
  const from = text("from");
  const to = text("to");
  if (from && to) parts.push(`${humanize(from)} → ${humanize(to)}`);
  else if (to) parts.push(`→ ${humanize(to)}`);
  const version = text("agent_version");
  if (version) parts.push(`agent version ${version}`);
  const attempt = text("attempt_number");
  if (attempt) parts.push(`attempt ${attempt}`);
  if (Array.isArray(p.fields)) parts.push(`changed ${p.fields.map(String).join(", ")}`);
  const actor = text("actor");
  if (actor) parts.push(`by ${actor.toLowerCase()}`);
  const reason = text("reason");
  if (reason) parts.push(`“${reason}”`);
  const prefix = taskTitle ? `${taskTitle}: ` : "";
  return prefix + (parts.join(" · ") || humanize(event.event_type));
}

export function EventTimeline({ events, tasks }: { events: WorkflowEvent[]; tasks: Task[] }) {
  const titles = new Map(tasks.map((t) => [t.id, t.title]));
  if (events.length === 0) return <p className="text-sm text-muted-foreground">No events yet.</p>;
  const newestFirst = [...events].reverse();
  return (
    <ol className="relative space-y-4 border-l pl-6" aria-label="Workflow events, newest first">
      {newestFirst.map((event) => (
        <li key={event.id} className="relative">
          <span className="absolute top-1.5 -left-[1.69rem] size-2.5 rounded-full border-2 border-background bg-primary" aria-hidden />
          <p className="text-sm font-medium">{humanize(event.event_type)}</p>
          <p className="text-sm break-words text-muted-foreground">
            {describeEvent(event, event.task_id ? titles.get(event.task_id) : undefined)}
          </p>
          <p className="text-xs text-muted-foreground">
            #{event.sequence} · <RelativeTime value={event.created_at} />
          </p>
        </li>
      ))}
    </ol>
  );
}
