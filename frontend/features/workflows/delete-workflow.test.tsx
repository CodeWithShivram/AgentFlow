import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Workflow } from "@/lib/api/types";
import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { DeleteWorkflowAction } from "./delete-workflow";

const router = { replace: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const WORKFLOW = {
  id: "wf-9",
  workspace_id: "ws-1",
  prompt: "Write a memo",
  status: "COMPLETED",
  revision: 3,
  kind: "GENERAL",
} as Workflow;

function renderAction(workflow: Workflow) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <WorkspaceProvider value={{ id: "ws-1", name: "W", role: "ADMIN" }}>
        <DeleteWorkflowAction workflow={workflow} />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => router.replace.mockReset());
afterEach(() => vi.unstubAllGlobals());

describe("DeleteWorkflowAction", () => {
  it("deletes a finished workflow after confirmation and returns to Workflows", async () => {
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(null, { status: 204 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderAction(WORKFLOW);

    await userEvent.click(screen.getByRole("button", { name: "Delete workflow" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Usage and cost history stay in analytics");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete workflow" }));

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/w/ws-1/workflows"));
    const [url, init] = fetchMock.mock.calls[0];
    expect(init?.method).toBe("DELETE");
    expect(new URL(url).pathname + new URL(url).search).toBe("/api/v1/workspaces/ws-1/workflows/wf-9?revision=3");
  });

  it("offers Cancel & delete while the workflow is still at work", () => {
    renderAction({ ...WORKFLOW, status: "RUNNING" });
    expect(screen.getByRole("button", { name: "Cancel & delete" })).toBeInTheDocument();
  });
});
