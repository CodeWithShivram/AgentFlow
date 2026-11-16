import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApprovalDecision, WorkspaceRole } from "@/lib/api/types";
import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { ApprovalsView } from "./approvals-view";

vi.mock("next/navigation", () => ({
  usePathname: () => "/w/ws-1/approvals",
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const DECISION: ApprovalDecision = {
  id: "d-1",
  intervention_id: "i-1",
  workflow_id: "wf-1",
  task_id: "t-1",
  execution_id: "e-1",
  subject: "agent_action",
  requested_action: "<b>Email the customer list</b>",
  workflow_prompt: "Plan a launch",
  decision: "reject",
  then: "stop",
  note: "Use the anonymised list.",
  required_role: "ADMIN",
  decided_by_user_id: "u-1",
  decided_role: "ADMIN",
  expired: false,
  by_workflow_creator: false,
  action_fingerprint: "cd".repeat(32),
  decided_at: new Date().toISOString(),
};

function renderView(role: WorkspaceRole) {
  const fetchMock = vi.fn<(input: string) => Promise<Response>>(
    async () =>
      new Response(JSON.stringify({ items: [DECISION], total: 1, limit: 20, offset: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider client={createQueryClient()}>
      <WorkspaceProvider value={{ id: "ws-1", name: "W", role }}>
        <ApprovalsView />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("ApprovalsView", () => {
  it("lists decisions with their audit for admins, as plain text", async () => {
    const fetchMock = renderView("ADMIN");

    const item = await screen.findByRole("article", { name: "Agent asks for approval" });
    expect(item).toHaveTextContent("Rejected and stopped: Agent asks for approval");
    expect(item).toHaveTextContent("<b>Email the customer list</b>");
    expect(item.querySelector("b")).toBeNull();
    expect(item).toHaveTextContent("Note: Use the anonymised list.");
    expect(item).toHaveTextContent("Decided as Admin · needed Admin · fingerprint cdcdcdcdcdcd…");
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(/\/workspaces\/ws-1\/approvals/);
  });

  it("is for admins only", () => {
    const fetchMock = renderView("MEMBER");
    expect(screen.getByText("Admins only")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
