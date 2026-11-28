import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Workflow, WorkspaceRole } from "@/lib/api/types";
import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { BuildForMe, nextAutopilotStep } from "./build-for-me";

const BASE = {
  id: "wf-1",
  workspace_id: "ws-1",
  prompt: "Build a recipe site",
  status: "PENDING",
  analysis_status: "NOT_REQUESTED",
  plan_status: "NOT_REQUESTED",
  confirmed_plan_version: null,
  revision: 1,
  kind: "SOFTWARE_PROJECT",
} as Workflow;

function state(changes: Partial<Workflow>): Workflow {
  return { ...BASE, ...changes };
}

afterEach(() => vi.unstubAllGlobals());

describe("nextAutopilotStep", () => {
  it.each<[Partial<Workflow>, WorkspaceRole, string, string]>([
    [{}, "MEMBER", "transition", "Starting planning…"],
    [{ status: "PLANNING", analysis_status: "PENDING", plan_status: "NOT_REQUESTED" }, "MEMBER", "wait", "Planning…"],
    [{ status: "PLANNING", analysis_status: "READY", plan_status: "PENDING" }, "MEMBER", "wait", "Planning…"],
    [{ status: "PLANNING", analysis_status: "READY", plan_status: "READY" }, "MEMBER", "confirm", "Confirming the plan…"],
    [{ status: "PLANNING", plan_status: "READY", confirmed_plan_version: 1 }, "MEMBER", "transition", "Sending the plan for approval…"],
    [{ status: "PLANNING", plan_status: "FAILED" }, "MEMBER", "stop", "Planning failed. Retry it from Plan & controls."],
    [{ status: "PLANNING", analysis_status: "READY", plan_status: "NOT_REQUESTED" }, "MEMBER", "stop", "The planner has questions for you. Answer them under Requests."],
    [{ status: "AWAITING_APPROVAL" }, "MEMBER", "stop", "Waiting for an admin to approve the plan."],
    [{ status: "AWAITING_APPROVAL" }, "ADMIN", "transition", "Approving the plan…"],
    [{ status: "READY" }, "MEMBER", "start", "Starting the build…"],
    [{ status: "RUNNING" }, "MEMBER", "done", "Building. Progress updates below."],
  ])("%o as %s: %s", (changes, role, kind, label) => {
    expect(nextAutopilotStep(state(changes), role)).toMatchObject({ kind, label });
  });
});

describe("BuildForMe", () => {
  it("takes a new project from PENDING to a running build through the API, as an admin", async () => {
    const sequence: Workflow[] = [
      state({}),
      state({ status: "PLANNING", analysis_status: "READY", plan_status: "READY", revision: 2 }),
      state({ status: "PLANNING", plan_status: "READY", confirmed_plan_version: 1, revision: 3 }),
      state({ status: "AWAITING_APPROVAL", revision: 4 }),
      state({ status: "READY", revision: 5 }),
      state({ status: "RUNNING", revision: 6 }),
    ];
    let index = 0;
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async (url, init) => {
      const path = new URL(url).pathname;
      const json = (body: unknown) =>
        new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
      if (!init?.method || init.method === "GET") {
        if (path.endsWith("/plan-drafts")) return json({ items: [{ version: 1 }] });
        if (path.endsWith("/plan-drafts/1")) return json({ version: 1, issues: [{ severity: "warning" }] });
        return json(sequence[Math.min(index, sequence.length - 1)]);
      }
      index += 1;
      return json(sequence[index]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <QueryClientProvider client={createQueryClient()}>
        <WorkspaceProvider value={{ id: "ws-1", name: "W", role: "ADMIN" }}>
          <BuildForMe workflow={BASE} />
        </WorkspaceProvider>
      </QueryClientProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: "Build it for me" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Build it" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Building. Progress updates below."));

    const writes = fetchMock.mock.calls
      .filter(([, init]) => init?.method === "POST")
      .map(([url, init]) => [new URL(url).pathname.replace("/api/v1/workspaces/ws-1/workflows/wf-1", ""), JSON.parse(String(init?.body))]);
    expect(writes).toEqual([
      ["/transitions", { to_status: "PLANNING", revision: 1 }],
      ["/plan-drafts/1/confirm", { revision: 2, acknowledge_warnings: true }],
      ["/transitions", { to_status: "AWAITING_APPROVAL", revision: 3 }],
      ["/transitions", { to_status: "READY", revision: 4 }],
      ["/start", { revision: 5 }],
    ]);
  });

  it("stops at plan errors instead of confirming", async () => {
    const planned = state({ status: "PLANNING", analysis_status: "READY", plan_status: "READY" });
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async (url) => {
      const path = new URL(url).pathname;
      const json = (body: unknown) =>
        new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
      if (path.endsWith("/plan-drafts")) return json({ items: [{ version: 2 }, { version: 1 }] });
      if (path.endsWith("/plan-drafts/2")) return json({ version: 2, issues: [{ severity: "error" }] });
      return json(planned);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <QueryClientProvider client={createQueryClient()}>
        <WorkspaceProvider value={{ id: "ws-1", name: "W", role: "MEMBER" }}>
          <BuildForMe workflow={planned} />
        </WorkspaceProvider>
      </QueryClientProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Build it for me" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Build it" }));
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("The plan has errors. Review it in Plan & controls."),
    );
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });
});
