"use client";

import { useState } from "react";

import { RelativeTime } from "@/components/common/relative-time";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { Task, WorkflowEvent } from "@/lib/api/types";
import { humanize } from "@/lib/format";

/** Timeline categories (ADR-0042); the backend's `event_categories.CATEGORIES` mirrors this. */
export type EventCategory = "status" | "planning" | "verification" | "people" | "recovery";

const CATEGORY_LABELS: Record<EventCategory, string> = {
  status: "Status changes",
  planning: "Analysis and plans",
  verification: "Submissions and checks",
  people: "Requests for people",
  recovery: "Retries and recovery",
};

const BY_TYPE: Record<string, EventCategory> = {
  ANALYSIS_REQUESTED: "planning",
  REQUIREMENTS_ANALYZED: "planning",
  ANALYSIS_FAILED: "planning",
  PLAN_REQUESTED: "planning",
  PLAN_GENERATED: "planning",
  PLAN_GENERATION_FAILED: "planning",
  PLAN_CREATED: "planning",
  PLAN_CONFIRMED: "planning",
  AGENT_SUBMITTED: "verification",
  VERIFICATION_STARTED: "verification",
  VERIFICATION_PASSED: "verification",
  VERIFICATION_FAILED: "verification",
  APPROVAL_REQUESTED: "people",
  APPROVAL_RESOLVED: "people",
  INTERVENTION_OPENED: "people",
  INTERVENTION_RESOLVED: "people",
  RETRY_REQUESTED: "recovery",
  TASK_REASSIGNED: "recovery",
  BACKGROUND_JOB_RETRIED: "recovery",
};

export function eventCategory(eventType: string): EventCategory {
  return BY_TYPE[eventType] ?? "status";
}

const SUBJECTS: Record<string, string> = {
  analysis_question: "question about the request",
  plan_approval: "plan approval",
  agent_action: "agent's approval request",
  task_escalation: "escalated task",
};

/** A one-line, plain-text summary of an event payload. Values are never rendered as HTML. */
export function describeEvent(event: WorkflowEvent, taskTitle?: string): string {
  const p = event.payload;
  const parts: string[] = [];
  const text = (key: string) => (typeof p[key] === "string" || typeof p[key] === "number" ? String(p[key]) : null);
  const codes = (key: string) => (Array.isArray(p[key]) ? p[key].map(String) : []);
  const from = text("from");
  const to = text("to");
  if (from && to) parts.push(`${humanize(from)} → ${humanize(to)}`);
  else if (to) parts.push(`→ ${humanize(to)}`);
  const subject = text("subject");
  if (subject) parts.push(SUBJECTS[subject] ?? humanize(subject));
  const decision = text("decision");
  if (decision) parts.push(decision === "approve" ? "approved" : text("then") === "stop" ? "rejected, task stopped" : "rejected");
  const outcome = text("outcome");
  if (outcome) parts.push(humanize(outcome));
  const pauseReason = text("pause_reason");
  if (pauseReason) parts.push(`pause: ${humanize(pauseReason)}`);
  const version = text("agent_version");
  if (version) parts.push(`agent version ${version}`);
  const attempt = text("attempt_number");
  if (attempt) parts.push(`attempt ${attempt}`);
  if (Array.isArray(p.fields)) parts.push(`changed ${p.fields.map(String).join(", ")}`);
  const job = text("event_type");
  if (job) parts.push(`job ${job}`);
  const actor = text("actor");
  if (actor) parts.push(`by ${actor.toLowerCase()}`);
  const reason = text("reason");
  if (reason) parts.push(`“${reason}”`);
  const errorCode = text("error_code");
  if (errorCode) parts.push(humanize(errorCode));
  const issues = [...codes("failure_codes"), ...codes("issue_codes")];
  if (issues.length > 0) parts.push(issues.map(humanize).join(", "));
  const prefix = taskTitle ? `${taskTitle}: ` : "";
  return prefix + (parts.join(" · ") || humanize(event.event_type));
}

/** Newest first, optionally one category's and one task's events. */
export function filterEvents(
  events: WorkflowEvent[],
  category: EventCategory | "all",
  taskId: string,
): WorkflowEvent[] {
  return [...events]
    .reverse()
    .filter((e) => category === "all" || eventCategory(e.event_type) === category)
    .filter((e) => taskId === "all" || e.task_id === taskId);
}

function dayOf(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" });
}

export function EventTimeline({ events, tasks }: { events: WorkflowEvent[]; tasks: Task[] }) {
  const [category, setCategory] = useState<EventCategory | "all">("all");
  const [taskId, setTaskId] = useState<string>("all");
  const titles = new Map(tasks.map((t) => [t.id, t.title]));
  if (events.length === 0) return <p className="text-sm text-muted-foreground">No events yet.</p>;
  const shown = filterEvents(events, category, taskId);
  const days: { day: string; items: WorkflowEvent[] }[] = [];
  for (const event of shown) {
    const day = dayOf(event.created_at);
    if (days.at(-1)?.day !== day) days.push({ day, items: [] });
    days.at(-1)!.items.push(event);
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-4">
        <div className="grid gap-1.5">
          <Label htmlFor="timeline-category">Show</Label>
          <Select value={category} onValueChange={(v) => setCategory(v as EventCategory | "all")}>
            <SelectTrigger id="timeline-category" className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All events</SelectItem>
              {(Object.keys(CATEGORY_LABELS) as EventCategory[]).map((c) => (
                <SelectItem key={c} value={c}>
                  {CATEGORY_LABELS[c]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {tasks.length > 0 ? (
          <div className="grid gap-1.5">
            <Label htmlFor="timeline-task">Task</Label>
            <Select value={taskId} onValueChange={setTaskId}>
              <SelectTrigger id="timeline-task" className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All tasks and the workflow</SelectItem>
                {tasks.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : null}
      </div>
      {shown.length === 0 ? (
        <p className="text-sm text-muted-foreground">No events match these filters.</p>
      ) : (
        <ol className="space-y-6" aria-label="Workflow events, newest first">
          {days.map(({ day, items }) => (
            <li key={day}>
              <h3 className="mb-2 text-sm font-medium text-muted-foreground">{day}</h3>
              <ol className="relative space-y-4 border-l pl-6">
                {items.map((event) => (
                  <li key={event.id} className="relative">
                    <span
                      className="absolute top-1.5 -left-[1.69rem] size-2.5 rounded-full border-2 border-background bg-primary"
                      aria-hidden
                    />
                    <p className="text-sm font-medium">{humanize(event.event_type)}</p>
                    <p className="text-sm break-words text-muted-foreground">
                      {describeEvent(event, event.task_id ? titles.get(event.task_id) : undefined)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      #{event.sequence} · {CATEGORY_LABELS[eventCategory(event.event_type)]} ·{" "}
                      <RelativeTime value={event.created_at} />
                    </p>
                  </li>
                ))}
              </ol>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
