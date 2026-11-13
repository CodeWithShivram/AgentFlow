import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Execution, Task } from "@/lib/api/types";
import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { ExecutionsPanel } from "./executions-panel";

const TASK = {
  id: "t-1",
  workflow_id: "wf-1",
  title: "Compare vendors",
  status: "SUBMITTED",
  acceptance_criteria: [
    { id: "c1", description: "Covers all vendors." },
    { id: "c2", description: "Cites sources." },
  ],
} as Task;

function execution(overrides: Partial<Execution> = {}): Execution {
  return {
    id: "e-1",
    task_id: "t-1",
    attempt_number: 1,
    status: "SUCCEEDED",
    agent_id: "a-1",
    agent_version_id: "v-1",
    agent_version: 1,
    started_at: new Date().toISOString(),
    completed_at: new Date().toISOString(),
    error_code: null,
    error_summary: null,
    result_summary: "Compared three vendors.",
    submission: {
      schema_version: 1,
      summary: "Compared three vendors.",
      outputs: {},
      criteria: [
        { criterion_id: "c1", status: "MET", evidence: "Table rows 1-3." },
        { criterion_id: "c2", status: "PARTIAL", evidence: "<b>Two</b> sources." },
      ],
      assumptions: [],
      limitations: ["Prices are list prices."],
      open_questions: ["Which region?"],
    },
    tool_invocations: [],
    ...overrides,
  };
}

function renderPanel(items: Execution[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ items }), { status: 200, headers: { "Content-Type": "application/json" } })),
  );
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <WorkspaceProvider value={{ id: "ws-1", name: "W", role: "VIEWER" }}>
        <ExecutionsPanel task={TASK} agentsById={new Map()} />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("ExecutionsPanel submissions", () => {
  it("shows the agent's unverified criterion claims, open questions and limitations as text", async () => {
    renderPanel([execution()]);

    const claims = await screen.findByRole("region", { name: "Agent's criterion claims" });
    expect(within(claims).getByText("Agent's claims (not verified yet)")).toBeInTheDocument();
    const items = within(claims).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("MetCovers all vendors.Table rows 1-3.");
    expect(items[1]).toHaveTextContent("Partly metCites sources.<b>Two</b> sources.");
    expect(claims.querySelector("b")).toBeNull(); // agent output is never rendered as HTML
    expect(within(screen.getByRole("region", { name: "Open questions" })).getByText("Which region?")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Limitations" })).getByText("Prices are list prices.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Assumptions" })).not.toBeInTheDocument();
  });

  it("shows nothing extra for attempts without a submission", async () => {
    renderPanel([
      execution({
        status: "FAILED",
        result_summary: null,
        submission: null,
        error_code: "INVALID_SUBMISSION",
        error_summary: "Invalid submission: MISSING_CRITERION_CLAIM",
      }),
    ]);

    expect(await screen.findByText("Invalid submission: MISSING_CRITERION_CLAIM")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Agent's criterion claims" })).not.toBeInTheDocument();
  });
});
