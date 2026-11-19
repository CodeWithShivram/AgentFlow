import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Task, WorkflowEvent } from "@/lib/api/types";

import { describeEvent, EventTimeline, eventCategory, filterEvents } from "./event-timeline";

function event(sequence: number, event_type: string, payload: Record<string, unknown>, extra: Partial<WorkflowEvent> = {}): WorkflowEvent {
  return {
    id: `e-${sequence}`,
    sequence,
    task_id: null,
    event_type,
    payload,
    created_at: "2026-11-18T10:00:00Z",
    ...extra,
  };
}

const EVENTS = [
  event(1, "WORKFLOW_CREATED", { actor: "USER" }),
  event(2, "TASK_STATUS_CHANGED", { from: "QUEUED", to: "IN_PROGRESS", actor: "WORKER" }, { task_id: "t-1" }),
  event(3, "INTERVENTION_OPENED", { subject: "agent_action", type: "APPROVAL" }, { task_id: "t-1" }),
  event(
    4,
    "INTERVENTION_RESOLVED",
    { decision: "reject", then: "stop", status: "RESOLVED" },
    { task_id: "t-1", created_at: "2026-11-19T09:00:00Z" },
  ),
  event(
    5,
    "WORKFLOW_STATUS_CHANGED",
    { from: "RUNNING", to: "PAUSED", actor: "SYSTEM", reason: "BUDGET_REACHED", pause_reason: "BUDGET_REACHED" },
    { created_at: "2026-11-19T10:00:00Z" },
  ),
  event(6, "BACKGROUND_JOB_RETRIED", { actor: "USER", event_type: "task.dispatch" }, { created_at: "2026-11-19T11:00:00Z" }),
];

describe("describeEvent", () => {
  it("describes requests, decisions, pauses and requeued jobs", () => {
    expect(describeEvent(EVENTS[2]!, "Draft")).toBe("Draft: agent's approval request");
    expect(describeEvent(EVENTS[3]!)).toBe("rejected, task stopped");
    expect(describeEvent(EVENTS[4]!)).toBe(
      "Running → Paused · pause: Budget reached · by system · “BUDGET_REACHED”",
    );
    expect(describeEvent(EVENTS[5]!)).toBe("job task.dispatch · by user");
    expect(describeEvent(event(9, "VERIFICATION_FAILED", { failure_codes: ["DELIVERABLE_MISSING"] }))).toBe(
      "Deliverable missing",
    );
  });
});

describe("filterEvents", () => {
  it("filters by category and task, newest first", () => {
    expect(eventCategory("INTERVENTION_RESOLVED")).toBe("people");
    expect(eventCategory("TASK_REASSIGNED")).toBe("recovery");
    expect(eventCategory("SOMETHING_NEW")).toBe("status");
    expect(filterEvents(EVENTS, "people", "all").map((e) => e.sequence)).toEqual([4, 3]);
    expect(filterEvents(EVENTS, "all", "t-1").map((e) => e.sequence)).toEqual([4, 3, 2]);
    expect(filterEvents(EVENTS, "recovery", "t-1")).toEqual([]);
  });
});

describe("EventTimeline", () => {
  it("groups events by day, newest first", () => {
    render(<EventTimeline events={EVENTS} tasks={[{ id: "t-1", title: "Draft" } as Task]} />);
    const list = screen.getByRole("list", { name: "Workflow events, newest first" });
    const days = within(list).getAllByRole("heading", { level: 3 });
    expect(days).toHaveLength(2);
    expect(list).toHaveTextContent("Background job retried");
    expect(list).toHaveTextContent("Draft: Queued → In progress · by worker");
    expect(screen.getByLabelText("Show")).toBeInTheDocument();
    expect(screen.getByLabelText("Task")).toBeInTheDocument();
  });
});
