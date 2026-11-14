import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentSuggestion, Task } from "@/lib/api/types";
import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { AssignAgentDialog } from "./assign-agent-dialog";

const TASK = { id: "t-1", workflow_id: "wf-1", title: "Draft notes", revision: 4, required_skills: [] } as unknown as Task;

function suggestion(name: string, overrides: Partial<AgentSuggestion> = {}): AgentSuggestion {
  return {
    agent_id: `id-${name}`,
    agent_name: name,
    agent_version_id: `v-${name}`,
    agent_version: 1,
    eligible: true,
    score: 70,
    rank: 1,
    factors: [
      { name: "skills", outcome: "pass", points: 0, detail: "Has every required skill." },
      { name: "skill_fit", outcome: "scored", points: 40, detail: "Required skills are 1 of its 1 skills." },
      { name: "workload", outcome: "scored", points: 30, detail: "0 tasks currently assigned or running." },
      { name: "history", outcome: "not_scored", points: 0, detail: "Not scored until verification." },
    ],
    ...overrides,
  };
}

const ITEMS = [
  suggestion("Writer"),
  suggestion("General", { score: 30, rank: 2 }),
  suggestion("Researcher", {
    eligible: false,
    score: 0,
    rank: null,
    factors: [{ name: "skills", outcome: "fail", points: 0, detail: "Missing required skills: report_writing." }],
  }),
];

function renderDialog() {
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(async (input) => {
    const body = input.endsWith("/agent-suggestions") ? { rules_version: "selection/v1", items: ITEMS } : { ...TASK, revision: 5 };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  const onAssigned = vi.fn();
  render(
    <QueryClientProvider client={createQueryClient()}>
      <WorkspaceProvider value={{ id: "ws-1", name: "W", role: "MEMBER" }}>
        <AssignAgentDialog task={TASK} workflowId="wf-1" onOpenChange={() => undefined} onAssigned={onAssigned} onError={() => undefined} />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
  return { fetchMock, onAssigned };
}

afterEach(() => vi.unstubAllGlobals());

describe("AssignAgentDialog suggestions", () => {
  it("ranks suitable agents with reasons and explains the excluded ones", async () => {
    renderDialog();

    const group = await screen.findByRole("radiogroup", { name: "Suitable agents" });
    const options = within(group).getAllByRole("radio");
    expect(options.map((o) => o.textContent)).toEqual([
      expect.stringContaining("Writer"),
      expect.stringContaining("General"),
    ]);
    expect(within(options[0]).getByText("Suggested")).toBeInTheDocument();
    expect(within(options[1]).queryByText("Suggested")).not.toBeInTheDocument();
    expect(options[0]).toHaveTextContent("Required skills are 1 of its 1 skills. 0 tasks currently assigned or running.");
    expect(options[0]).not.toHaveTextContent("Not scored");

    const excluded = screen.getByRole("region", { name: "Not suitable" });
    expect(excluded).toHaveTextContent("Researcher Missing required skills: report_writing.");
    expect(within(excluded).queryByRole("radio")).not.toBeInTheDocument();
  });

  it("assigns the agent the member picks, not necessarily the suggestion", async () => {
    document.cookie = "agentflow_csrf=t-1";
    const { fetchMock, onAssigned } = renderDialog();

    await userEvent.click(await screen.findByRole("radio", { name: /General/ }));
    await userEvent.click(screen.getByRole("button", { name: "Assign" }));

    await vi.waitFor(() => expect(onAssigned).toHaveBeenCalled());
    const call = fetchMock.mock.calls.find(([url]) => url.endsWith("/tasks/t-1/assign"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ agent_id: "id-General", revision: 4 });
  });
});
