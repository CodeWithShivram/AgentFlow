import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FailedJob, Task, WorkspaceRole } from "@/lib/api/types";
import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { FailedJobs } from "./failed-jobs";

const JOB: FailedJob = {
  id: "job-1",
  event_type: "task.dispatch",
  workflow_id: "wf-1",
  task_id: "t-1",
  attempts: 10,
  error_type: "ConnectionError",
  created_at: new Date().toISOString(),
  failed_at: new Date().toISOString(),
};

function renderJobs(role: WorkspaceRole, items: FailedJob[] = [JOB]) {
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(async (input) => {
    const body = input.endsWith("/retry") ? JOB : { items };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider client={createQueryClient()}>
      <WorkspaceProvider value={{ id: "ws-1", name: "W", role }}>
        <FailedJobs workflowId="wf-1" tasks={[{ id: "t-1", title: "Draft the notes" } as Task]} />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("FailedJobs", () => {
  it("shows failed jobs to everyone without letting members retry", async () => {
    renderJobs("MEMBER");

    const region = await screen.findByRole("region", { name: "Failed background jobs" });
    expect(region).toHaveTextContent("1 background job failed");
    expect(region).toHaveTextContent("Run task· Draft the notes");
    expect(region).toHaveTextContent("10 attempts · ConnectionError");
    expect(within(region).queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(region).toHaveTextContent("An admin can retry failed jobs.");
  });

  it("lets admins retry a job", async () => {
    document.cookie = "agentflow_csrf=t-1";
    const fetchMock = renderJobs("ADMIN");

    await userEvent.click(await screen.findByRole("button", { name: "Retry" }));

    await vi.waitFor(() =>
      expect(fetchMock.mock.calls.some(([url, init]) => url.endsWith("/failed-jobs/job-1/retry") && init?.method === "POST")).toBe(true),
    );
  });

  it("renders nothing when no job failed", async () => {
    const fetchMock = renderJobs("ADMIN", []);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByRole("region", { name: "Failed background jobs" })).not.toBeInTheDocument();
  });
});
