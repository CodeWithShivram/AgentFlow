import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Agent, Task } from "@/lib/api/types";
import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { DependencyGraph } from "./dependency-graph";

function task(id: string, title: string, dependsOn: string[] = [], extra: Partial<Task> = {}): Task {
  return {
    id,
    workflow_id: "wf-1",
    title,
    depends_on: dependsOn,
    priority: 0,
    required: true,
    removed: false,
    status: "PENDING",
    attempt_count: 0,
    required_skills: ["research"],
    assigned_agent_id: null,
    ...extra,
  } as Task;
}

const TASKS = [
  task("a", "Collect sources", [], { status: "COMPLETED", assigned_agent_id: "ag-1", attempt_count: 1 }),
  task("b", "Draft report", ["a"], { status: "IN_PROGRESS", assigned_agent_id: "ag-1", attempt_count: 2 }),
  task("c", "Check facts", ["a"]),
  task("d", "Publish", ["b", "c"]),
  task("e", "Optional appendix", [], { required: false }),
];
const AGENTS = new Map([["ag-1", { id: "ag-1", name: "Writer" } as Agent]]);

function renderGraph(tasks: Task[] = TASKS) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } })),
  );
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <WorkspaceProvider value={{ id: "ws-1", name: "W", role: "MEMBER" }}>
        <DependencyGraph tasks={tasks} agentsById={AGENTS} />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("DependencyGraph", () => {
  it("draws every dependency as an arrow and keeps a text equivalent on each card", () => {
    const { container } = renderGraph();
    expect(container.querySelectorAll("path[data-edge]")).toHaveLength(4);
    const stage2 = screen.getByRole("region", { name: "Stage 2" });
    const draft = within(stage2).getByRole("button", { name: /^Draft report/ });
    expect(draft).toHaveTextContent("Writer · attempts 2");
    expect(draft).toHaveTextContent("After: Collect sources");
    expect(screen.getByRole("button", { name: /^Optional appendix/ })).toHaveTextContent("Optional");
  });

  it("selecting a task highlights its whole chain and opens its details", async () => {
    renderGraph();
    await userEvent.click(screen.getByRole("button", { name: /^Publish/ }));
    const details = screen.getByRole("region", { name: "Task details" });
    expect(details).toHaveTextContent("Waits on directlyDraft report, Check facts");
    expect(details).toHaveTextContent("Waits on (all)");
    expect(details).toHaveTextContent("Collect sources");
    expect(details).toHaveTextContent("Waited on by (all)Nothing");
    expect(screen.getByRole("button", { name: /^Publish/ })).toHaveAttribute("aria-pressed", "true");
    // Unrelated tasks fade; the optional appendix is not in the chain.
    expect(screen.getByRole("button", { name: /^Optional appendix/ }).className).toContain("opacity-50");

    await userEvent.click(within(details).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("region", { name: "Task details" })).not.toBeInTheDocument();
  });

  it("shows the critical path of unfinished required tasks on request", async () => {
    renderGraph();
    expect(screen.queryByText(/Critical path:/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("switch", { name: "Show critical path" }));
    // Collect sources is finished; Draft report and Check facts tie, so the title decides.
    expect(screen.getByRole("status")).toHaveTextContent("Critical path: Check facts → Publish");
    expect(screen.getByRole("button", { name: /^Publish/ })).toHaveTextContent("Critical path");
    expect(screen.getByRole("button", { name: /^Draft report/ })).not.toHaveTextContent("Critical path");
    expect(screen.getByRole("button", { name: /^Collect sources/ })).not.toHaveTextContent("Critical path");
  });

  it("says so when everything required is finished, and when there are no tasks", async () => {
    const done = TASKS.map((t) => ({ ...t, status: "COMPLETED" as const }));
    const { unmount } = renderGraph(done);
    await userEvent.click(screen.getByRole("switch", { name: "Show critical path" }));
    expect(screen.getByRole("status")).toHaveTextContent("Every required task is finished.");
    unmount();
    renderGraph([]);
    expect(screen.getByText("No tasks yet.")).toBeInTheDocument();
  });
});
