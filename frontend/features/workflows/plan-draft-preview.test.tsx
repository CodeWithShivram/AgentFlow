import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PlanDraftPreview as Preview, PlannedTask, Workflow, WorkspaceRole } from "@/lib/api/types";
import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { PlanDraftPreview } from "./plan-draft-preview";

function task(key: string, title: string, depends_on: string[] = [], required = true): PlannedTask {
  return {
    key,
    title,
    description: `Do ${title}`,
    required_skills: ["research"],
    deliverables: [`${title} notes`],
    acceptance_criteria: [{ id: "c1", description: `${title} is complete` }],
    priority: 0,
    required,
    depends_on,
  };
}

function preview(overrides: Partial<Preview> = {}): Preview {
  return {
    version: 2,
    task_count: 3,
    prompt_version: "plan_generation/v1",
    created_at: new Date().toISOString(),
    plan: {
      schema_version: 1,
      summary: "Compare CRM vendors",
      tasks: [task("t1", "Collect pricing"), task("t2", "Collect reviews", [], false), task("t3", "Write report", ["t1", "t2"])],
      risks: ["Prices change often"],
    },
    stages: [["t1", "t2"], ["t3"]],
    issues: [],
    ...overrides,
  };
}

function stub(draft: Preview) {
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(async (input) => {
    const body = input.endsWith("/confirm")
      ? { id: "wf-1", status: "PLANNING", plan_status: "READY", revision: 8, confirmed_plan_version: draft.version }
      : input.endsWith("/plan-drafts")
      ? { items: [{ ...draft, plan: undefined }, { version: 1, task_count: 1, prompt_version: "v", created_at: draft.created_at }] }
      : draft;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const onConfirmed = vi.fn();

function renderPreview(
  wf: Partial<Workflow> = {},
  { role = "MEMBER", existingTaskCount = 0 }: { role?: WorkspaceRole; existingTaskCount?: number } = {},
) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <WorkspaceProvider value={{ id: "ws-1", name: "W", role }}>
        <PlanDraftPreview
          workflow={
            { id: "wf-1", status: "PLANNING", plan_status: "READY", revision: 7, confirmed_plan_version: null, ...wf } as Workflow
          }
          existingTaskCount={existingTaskCount}
          onConfirmed={onConfirmed}
          onConflict={() => undefined}
        />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
}

// The preview loads the version list, then the draft: two requests in sequence, which can
// exceed the default 1 s wait on a loaded machine.
const LOAD = { timeout: 5000 };

afterEach(() => vi.unstubAllGlobals());

describe("PlanDraftPreview", () => {
  it("shows the latest draft by stage with its tasks and dependencies", async () => {
    const fetchMock = stub(preview());
    renderPreview();

    const stage1 = await screen.findByRole("region", { name: "Draft stage 1" }, LOAD);
    expect(within(stage1).getByText("Collect pricing")).toBeInTheDocument();
    expect(within(stage1).getByText("Optional")).toBeInTheDocument();
    const stage2 = screen.getByRole("region", { name: "Draft stage 2" });
    expect(within(stage2).getByText("After: Collect pricing, Collect reviews")).toBeInTheDocument();
    expect(screen.getByText("No problems found.")).toBeInTheDocument();
    expect(screen.getByText("Prices change often")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/plan-drafts/2"))).toBe(true);
  });

  it("reveals deliverables and acceptance criteria on demand", async () => {
    stub(preview());
    renderPreview();
    await userEvent.click(await screen.findByText("Write report", {}, LOAD));
    expect(screen.getByText("Write report is complete")).toBeVisible();
    expect(screen.getByText("Write report notes")).toBeVisible();
  });

  it("lists warnings and errors with the affected task titles", async () => {
    stub(
      preview({
        issues: [
          { code: "SKILL_NOT_AVAILABLE", severity: "warning", message: "No active agent has research.", task_keys: ["t1"] },
          { code: "DEPENDENCY_CYCLE", severity: "error", message: "Cycle.", task_keys: ["t1", "t3"] },
        ],
      }),
    );
    renderPreview();
    expect(await screen.findByText("This draft cannot be used", {}, LOAD)).toBeInTheDocument();
    expect(screen.getByText("Cycle. (Collect pricing, Write report)")).toBeInTheDocument();
    expect(screen.getByText("Check before confirming (1)")).toBeInTheDocument();
    expect(screen.queryByText("No problems found.")).not.toBeInTheDocument();
  });

  it("explains when no draft exists yet", () => {
    renderPreview({ plan_status: "NOT_REQUESTED" });
    expect(screen.getByText("No plan draft yet")).toBeInTheDocument();
  });
});

describe("Confirming a draft", () => {
  const WARNED = preview({
    issues: [{ code: "SKILL_NOT_AVAILABLE", severity: "warning", message: "No active agent has research.", task_keys: ["t1"] }],
  });

  it("requires acknowledging warnings and choosing what happens to existing tasks", async () => {
    document.cookie = "agentflow_csrf=t-1";
    const fetchMock = stub(WARNED);
    renderPreview({}, { existingTaskCount: 2 });

    await userEvent.click(await screen.findByRole("button", { name: "Confirm this draft" }, LOAD));
    const dialog = screen.getByRole("dialog", { name: "Confirm draft version 2" });
    const confirm = within(dialog).getByRole("button", { name: "Confirm plan" });
    expect(confirm).toBeDisabled();
    expect(within(dialog).getByText("The workflow already has 2 tasks")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByLabelText(/Add the draft alongside/));
    await userEvent.click(within(dialog).getByRole("checkbox"));
    await userEvent.click(confirm);

    await vi.waitFor(() => expect(onConfirmed).toHaveBeenCalled());
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/plan-drafts/2/confirm"));
    expect(call).toBeDefined();
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      revision: 7,
      existing_tasks: "append",
      acknowledge_warnings: true,
    });
  });

  it("is not offered to viewers or for drafts with errors", async () => {
    stub(preview());
    const { unmount } = renderPreview({}, { role: "VIEWER" });
    await screen.findByRole("region", { name: "Draft stage 1" }, LOAD);
    expect(screen.queryByRole("button", { name: "Confirm this draft" })).not.toBeInTheDocument();
    unmount();

    stub(preview({ issues: [{ code: "DEPENDENCY_CYCLE", severity: "error", message: "Cycle.", task_keys: ["t1"] }] }));
    renderPreview();
    await screen.findByText("This draft cannot be used", {}, LOAD);
    expect(screen.queryByRole("button", { name: "Confirm this draft" })).not.toBeInTheDocument();
  });

  it("marks the confirmed version", async () => {
    stub(preview());
    renderPreview({ confirmed_plan_version: 2 });
    expect(await screen.findByText("Confirmed", {}, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm again" })).toBeInTheDocument();
  });
});
