import { describe, expect, it, vi } from "vitest";

import type { EventPage, Task, Workflow, WorkflowEvent } from "@/lib/api/types";

import { describeEvent } from "./event-timeline";
import { dependencyStages } from "./graph";
import { workflowActions } from "./status";
import { toCriteria } from "./task-form-dialog";
import { availableTaskActions } from "./task-list";
import { fetchNewEvents, pollDelay } from "./use-workflow";
import { toWorkflowInput, workflowSchema } from "./workflow-create-view";

function task(id: string, depends_on: string[] = [], extra: Partial<Task> = {}): Task {
  return {
    id,
    workflow_id: "wf",
    title: id,
    description: "",
    required_skills: [],
    input_references: [],
    expected_output_schema: null,
    acceptance_criteria: [],
    deliverables: [],
    priority: 0,
    required: true,
    removed: false,
    status: "PENDING",
    depends_on,
    assigned_agent_id: null,
    assigned_agent_version_id: null,
    attempt_count: 0,
    revision: 1,
    created_at: "",
    updated_at: "",
    started_at: null,
    completed_at: null,
    ...extra,
  };
}

const labels = (specs: { label: string }[]) => specs.map((s) => s.label);

describe("workflowActions", () => {
  it("offers approval only to admins", () => {
    expect(labels(workflowActions("AWAITING_APPROVAL", "approval_required", "ADMIN"))).toContain("Approve plan");
    expect(labels(workflowActions("AWAITING_APPROVAL", "approval_required", "MEMBER"))).not.toContain("Approve plan");
  });

  it("follows the autonomy policy when leaving planning", () => {
    expect(labels(workflowActions("PLANNING", "approval_required", "MEMBER"))).toEqual([
      "Submit plan for approval",
      "Cancel workflow",
    ]);
    expect(labels(workflowActions("PLANNING", "autonomous", "MEMBER"))).toEqual(["Mark plan ready", "Cancel workflow"]);
  });

  it("offers nothing to viewers or on terminal workflows", () => {
    expect(workflowActions("RUNNING", "autonomous", "VIEWER")).toEqual([]);
    expect(workflowActions("COMPLETED", "autonomous", "OWNER")).toEqual([]);
  });

  it("requires admin to unblock", () => {
    expect(labels(workflowActions("BLOCKED", "autonomous", "MEMBER"))).toEqual(["Pause", "Cancel workflow"]);
    expect(labels(workflowActions("BLOCKED", "autonomous", "ADMIN"))).toContain("Resume (unblock)");
  });
});

describe("availableTaskActions", () => {
  const wf = (status: Workflow["status"]) => ({ status }) as Workflow;

  it("allows plan edits only while planning", () => {
    expect(availableTaskActions(wf("PLANNING"), task("a"))).toEqual(["edit", "dependencies", "preassign", "remove"]);
    // Step 4.4: PENDING tasks can be pre-assigned while the workflow runs, too.
    expect(availableTaskActions(wf("RUNNING"), task("a"))).toEqual(["preassign", "cancel"]);
    expect(availableTaskActions(wf("COMPLETED"), task("a"))).toEqual([]);
    expect(availableTaskActions(wf("RUNNING"), task("a", [], { status: "READY" }))).toEqual(["assign", "cancel"]);
    expect(availableTaskActions(wf("RUNNING"), task("a", [], { status: "COMPLETED" }))).toEqual([]);
    expect(availableTaskActions(wf("PLANNING"), task("a", [], { removed: true }))).toEqual([]);
  });
});

describe("dependencyStages", () => {
  it("places tasks after everything they depend on", () => {
    const stages = dependencyStages([task("c", ["b"]), task("b", ["a"]), task("a"), task("d", ["a", "c"])]);
    expect(stages.map((s) => s.map((t) => t.id))).toEqual([["a"], ["b"], ["c"], ["d"]]);
  });

  it("stays finite on a cycle", () => {
    const stages = dependencyStages([task("a", ["b"]), task("b", ["a"])]);
    expect(stages.flat()).toHaveLength(2);
  });
});

describe("event polling", () => {
  const event = (sequence: number): WorkflowEvent => ({
    id: `e${sequence}`,
    sequence,
    task_id: null,
    event_type: "TASK_STATUS_CHANGED",
    payload: { from: "QUEUED", to: "IN_PROGRESS", actor: "WORKER" },
    created_at: "",
  });

  it("fetches only events after the last one and follows pages", async () => {
    const load = vi.fn(async (after: number): Promise<EventPage> => {
      if (after === 1) {
        const items = Array.from({ length: 500 }, (_, i) => event(i + 2));
        return { items, last_sequence: 501 };
      }
      return { items: [event(502)], last_sequence: 502 };
    });
    const events = await fetchNewEvents(load, [event(1)]);
    expect(load.mock.calls.map((c) => c[0])).toEqual([1, 501]);
    expect(events).toHaveLength(502);
  });

  it("backs off while nothing changes and after failures", () => {
    expect(pollDelay(0, false)).toBe(2000);
    expect(pollDelay(2, false)).toBe(4500);
    expect(pollDelay(20, false)).toBe(15000);
    expect(pollDelay(0, true)).toBe(15000);
  });

  it("describes events as plain text", () => {
    expect(describeEvent(event(1), "Draft")).toBe("Draft: Queued → In progress · by worker");
  });
});

describe("workflow form", () => {
  const base = {
    prompt: "Goal",
    autonomyPolicy: "approval_required" as const,
    budgetLimit: "",
    currency: "usd",
    maxParallelTasks: "3",
    allowDynamicAgentProposals: false,
    deadline: "",
  };

  it("maps to the API's budget_limit naming (OD-5) and upper-cases the currency", () => {
    const values = workflowSchema.parse({ ...base, budgetLimit: "125.5" });
    expect(toWorkflowInput(values).settings).toMatchObject({ budget_limit: "125.5", currency: "USD", max_parallel_tasks: 3 });
    expect(toWorkflowInput(workflowSchema.parse(base)).settings.budget_limit).toBeNull();
  });

  it.each([
    [{ prompt: "   " }],
    [{ budgetLimit: "-1" }],
    [{ budgetLimit: "1.23456" }],
    [{ budgetLimit: "1000001" }],
    [{ currency: "EURO" }],
    [{ maxParallelTasks: "0" }],
    [{ deadline: "2000-01-01T00:00" }],
  ])("rejects %o", (override) => {
    expect(workflowSchema.safeParse({ ...base, ...override }).success).toBe(false);
  });

  it("turns acceptance criteria lines into slugs", () => {
    expect(toCriteria("First\n\n  Second  \n")).toEqual([
      { id: "c1", description: "First" },
      { id: "c2", description: "Second" },
    ]);
  });
});
