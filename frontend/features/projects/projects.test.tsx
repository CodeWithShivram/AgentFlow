import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  IntegrationRun,
  Page,
  ProjectPackage,
  ProjectProgress,
  Task,
  Workflow,
  WorkspaceRole,
} from "@/lib/api/types";
import { PROJECT_PHASES } from "@/lib/api/types";
import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { Deliverables } from "./deliverables";
import { describeRemaining, formatRemainingRange, PHASE_LABELS, progressIsLive, projectName } from "./phase";
import { ProjectCreateView } from "./project-create-view";
import { ProjectDetailView } from "./project-detail-view";
import { PhaseBadge, ProgressSummary } from "./project-progress";
import { ProjectsListView } from "./projects-list-view";
import { latestPreview, UiPreview } from "./ui-preview";

const router = { replace: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => "/w/ws-1/projects",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ workspaceId: "ws-1", projectId: "wf-1" }),
}));

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function renderWith(ui: React.ReactNode, role: WorkspaceRole = "MEMBER") {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <WorkspaceProvider value={{ id: "ws-1", name: "W", role }}>{ui}</WorkspaceProvider>
    </QueryClientProvider>,
  );
}

const PROGRESS: ProjectProgress = {
  phase: "BUILDING",
  percent: 42,
  explanation: "5 of 12 weighted points verified complete.",
  tasks: { total: 6, completed: 2, in_progress: 1, waiting: 3, needs_attention: 0 },
  completed_weight: 5,
  total_weight: 12,
  integration: "pending",
  package: "pending",
  open_requests: 0,
  remaining: {
    low_seconds: 240,
    high_seconds: 540,
    confidence: "low",
    samples: 2,
    explanation: "Based on 2 completed tasks.",
  },
};

const WORKFLOW: Workflow = {
  id: "wf-1",
  workspace_id: "ws-1",
  created_by_user_id: "u-1",
  prompt: "Build a <b>todo</b> API with FastAPI",
  status: "BLOCKED",
  analysis_status: "READY",
  normalized_requirements: null,
  plan_status: "READY",
  confirmed_plan_version: 1,
  next_step: null,
  autonomy_policy: "approval_required",
  budget_limit: null,
  currency: "USD",
  max_parallel_tasks: 3,
  allow_dynamic_agent_proposals: false,
  deadline: null,
  revision: 4,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  started_at: null,
  completed_at: null,
  kind: "SOFTWARE_PROJECT",
  project_spec: { name: "Todo API", stack_profile: "python-fastapi", architecture: "One service." },
};

function pkg(extra: Partial<ProjectPackage> = {}): ProjectPackage {
  return {
    id: "p-1",
    workflow_id: "wf-1",
    head_sha: "abcdef1234567890",
    label: "VERIFIED",
    status: "READY",
    error_code: null,
    archive_artifact_id: "a-1",
    report_artifact_id: "r-1",
    file_count: 14,
    excluded_count: 2,
    created_at: "2026-11-20T10:00:00Z",
    ...extra,
  };
}

beforeEach(() => {
  router.replace.mockReset();
  router.push.mockReset();
});
afterEach(() => vi.unstubAllGlobals());

describe("phase and estimate rules", () => {
  it("labels every phase for people", () => {
    expect(PROJECT_PHASES.map((p) => PHASE_LABELS[p])).toEqual([
      "Planning",
      "Awaiting approval",
      "Building",
      "Integrating",
      "Packaging",
      "Completed",
      "Needs attention",
      "Failed",
      "Cancelled",
    ]);
  });

  it("always shows a range, rounding outwards, never a single number", () => {
    expect(formatRemainingRange(240, 540)).toBe("about 4–9 min");
    expect(formatRemainingRange(250, 260)).toBe("about 4–5 min");
    expect(formatRemainingRange(3 * 3600, 7.2 * 3600)).toBe("about 3–8 h");
    expect(formatRemainingRange(3 * 86_400, 5 * 86_400)).toBe("about 3–5 days");
    expect(formatRemainingRange(10, 40)).toBe("under a minute");
  });

  it("gives the API's explanation instead of numbers without confidence", () => {
    expect(
      describeRemaining({ low_seconds: null, high_seconds: null, confidence: "none", samples: 0, explanation: "No task has finished yet." }),
    ).toEqual({ range: null, confidence: null, explanation: "No task has finished yet." });
  });

  it("polls until finished, and after completion only while a package is being made", () => {
    expect(progressIsLive(PROGRESS)).toBe(true);
    expect(progressIsLive({ ...PROGRESS, phase: "COMPLETED", package: "ready" })).toBe(false);
    expect(progressIsLive({ ...PROGRESS, phase: "COMPLETED", package: "running" })).toBe(true);
    expect(progressIsLive({ ...PROGRESS, phase: "CANCELLED" })).toBe(false);
    expect(progressIsLive(undefined)).toBe(false);
  });

  it("names a project after its spec, else its prompt", () => {
    expect(projectName(WORKFLOW)).toBe("Todo API");
    expect(projectName({ prompt: "  Build\n a   shop ", project_spec: null })).toBe("Build a shop");
    expect(projectName({ prompt: "x".repeat(100), project_spec: null })).toHaveLength(80);
  });
});

describe("ProgressSummary", () => {
  it("shows an accessible estimate bar, its explanation and a ranged ETA with confidence", () => {
    render(<ProgressSummary progress={PROGRESS} />);
    const bar = screen.getByRole("progressbar", { name: "Estimated progress" });
    expect(bar).toHaveAttribute("aria-valuenow", "42");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
    expect(bar).toHaveAttribute("aria-valuetext", "42% (estimate)");
    expect(screen.getByText("Estimate")).toBeInTheDocument();
    expect(screen.getByText("5 of 12 weighted points verified complete.")).toBeInTheDocument();
    expect(screen.getByTestId("remaining-time")).toHaveTextContent("about 4–9 min (low confidence)");
    expect(screen.getByLabelText("Task counts")).toHaveTextContent("Total6Completed2In progress1Waiting3Need attention0");
  });

  it("explains a missing estimate instead of inventing one", () => {
    render(
      <ProgressSummary
        progress={{
          ...PROGRESS,
          remaining: { low_seconds: null, high_seconds: null, confidence: "none", samples: 0, explanation: "No task has finished yet." },
        }}
      />,
    );
    const remaining = screen.getByTestId("remaining-time");
    expect(remaining).toHaveTextContent("No task has finished yet.");
    expect(remaining).not.toHaveTextContent("about");
    expect(remaining).not.toHaveTextContent("confidence");
  });

  it("shows phase badges with human labels", () => {
    render(<PhaseBadge phase="NEEDS_ATTENTION" />);
    expect(screen.getByText("Needs attention")).toBeInTheDocument();
  });
});

describe("Deliverables", () => {
  it("tells verified and partial packages apart and links downloads to the artifacts API", () => {
    renderWith(
      <Deliverables
        workflowId="wf-1"
        packages={[pkg({ id: "p-2", label: "PARTIAL", archive_artifact_id: "a-2", report_artifact_id: "r-2" }), pkg()]}
      />,
    );
    const partial = screen.getByRole("listitem", { name: "Partial package" });
    expect(partial).toHaveTextContent("Partial: this state did not pass the integration checks.");
    expect(within(partial).getByRole("link", { name: "Download ZIP" })).toHaveAttribute(
      "href",
      "http://localhost:8000/api/v1/workspaces/ws-1/workflows/wf-1/artifacts/a-2/download",
    );

    const verified = screen.getByRole("listitem", { name: "Verified package" });
    expect(verified).toHaveTextContent("This state passed every integration check.");
    expect(verified).toHaveTextContent("14 files · 2 excluded");
    expect(verified).not.toHaveTextContent("Partial:");
    const zip = within(verified).getByRole("link", { name: "Download ZIP" });
    expect(zip).toHaveAttribute("href", "http://localhost:8000/api/v1/workspaces/ws-1/workflows/wf-1/artifacts/a-1/download");
    expect(zip).toHaveAttribute("download");
    expect(within(verified).getByRole("link", { name: "Build report" })).toHaveAttribute(
      "href",
      "http://localhost:8000/api/v1/workspaces/ws-1/workflows/wf-1/artifacts/r-1/download",
    );
  });

  it("explains failed packages and offers no download", () => {
    renderWith(
      <Deliverables
        workflowId="wf-1"
        packages={[pkg({ status: "FAILED", label: "PARTIAL", error_code: "SECRET_DETECTED", archive_artifact_id: null, report_artifact_id: null })]}
      />,
    );
    const item = screen.getByRole("listitem", { name: "Partial package" });
    expect(item).toHaveTextContent("Failed");
    expect(item).toHaveTextContent("SECRET_DETECTED");
    expect(item).toHaveTextContent("A likely secret was found in the project; nothing was packaged.");
    expect(within(item).queryByRole("link")).toBeNull();
  });
});

describe("ProjectCreateView", () => {
  // The settings switch (Radix) measures itself; jsdom has no ResizeObserver.
  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  });

  it("requires a description before calling the API", async () => {
    const fetchMock = vi.fn(async () => json(500, {}));
    vi.stubGlobal("fetch", fetchMock);
    renderWith(<ProjectCreateView />);
    await userEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByText("Describe the goal.")).toBeInTheDocument();
    expect(screen.getByLabelText("What should it build?")).toHaveAttribute("aria-invalid", "true");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("creates a SOFTWARE_PROJECT with an idempotency key and opens the project", async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () =>
      json(201, { ...WORKFLOW, id: "wf-9" }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWith(<ProjectCreateView />);
    expect(screen.getByText("Advanced settings")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("What should it build?"), "A todo API");
    await userEvent.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/w/ws-1/projects/wf-9"));
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toMatch(/\/api\/v1\/workspaces\/ws-1\/workflows$/);
    expect(init.method).toBe("POST");
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(body).toMatchObject({ prompt: "A todo API", kind: "SOFTWARE_PROJECT" });
    expect(body.settings).toMatchObject({ autonomy_policy: "approval_required", max_parallel_tasks: 3 });
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("is not offered to viewers", () => {
    renderWith(<ProjectCreateView />, "VIEWER");
    expect(screen.getByText("You cannot create projects")).toBeInTheDocument();
  });
});

describe("ProjectsListView", () => {
  it("lists only software projects and invites creating the first one", async () => {
    const empty: Page<Workflow> = { items: [], total: 0, limit: 20, offset: 0 };
    const fetchMock = vi.fn<(url: string) => Promise<Response>>(async () => json(200, empty));
    vi.stubGlobal("fetch", fetchMock);
    renderWith(<ProjectsListView />);
    expect(await screen.findByText("No projects yet")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "New project" }).at(-1)).toHaveAttribute("href", "/w/ws-1/projects/new");
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(/\/workspaces\/ws-1\/workflows\?.*kind=SOFTWARE_PROJECT/);
  });

  it("names projects after their spec and links to the project page", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { items: [WORKFLOW], total: 1, limit: 20, offset: 0 })));
    renderWith(<ProjectsListView />);
    expect(await screen.findByRole("link", { name: "Todo API" })).toHaveAttribute("href", "/w/ws-1/projects/wf-1");
  });
});

describe("ProjectDetailView", () => {
  const task: Partial<Task> = {
    id: "t-1",
    workflow_id: "wf-1",
    title: "Create the API",
    status: "COMPLETED",
    removed: false,
    priority: 1,
    file_scope: ["app/main.py", "app/models.py"],
    effort_points: 3,
  };
  const run: IntegrationRun = {
    id: "i-1",
    workflow_id: "wf-1",
    head_sha: "abcdef1234567890",
    stack_profile: "python-fastapi",
    status: "FAILED",
    checks: [
      { name: "install", outcome: "passed", exit_code: 0, duration_ms: 1200 },
      { name: "test", outcome: "failed", exit_code: 1, output: "<script>alert(1)</script> 1 failed" },
    ],
    error_code: null,
    created_at: new Date().toISOString(),
    finished_at: new Date().toISOString(),
  };

  function stubApi(progress: ProjectProgress) {
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async (url) => {
      const path = new URL(url).pathname;
      if (path.endsWith("/workflows/wf-1")) return json(200, WORKFLOW);
      if (path.endsWith("/progress")) return json(200, progress);
      if (path.endsWith("/tasks")) return json(200, { items: [task], total: 1, limit: 100, offset: 0 });
      if (path.endsWith("/integrations")) return json(200, [run]);
      if (path.endsWith("/packages")) return json(200, []);
      return json(404, {});
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("shows phase, progress, tasks, integration output as plain text and a needs-attention callout", async () => {
    stubApi({ ...PROGRESS, phase: "NEEDS_ATTENTION", open_requests: 2 });
    renderWith(<ProjectDetailView />, "ADMIN");

    expect(await screen.findByRole("heading", { level: 1, name: "Todo API" })).toBeInTheDocument();
    expect(screen.getByText("Build a <b>todo</b> API with FastAPI")).toBeInTheDocument();
    expect(await screen.findByRole("progressbar")).toHaveAttribute("aria-valuenow", "42");
    expect(screen.getAllByText("Needs attention").length).toBeGreaterThan(0);
    expect(screen.getByText(/2 open requests are waiting for a person/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open requests" })).toHaveAttribute("href", "/w/ws-1/requests");
    expect(screen.getAllByRole("link", { name: "Plan & controls" })[0]).toHaveAttribute("href", "/w/ws-1/workflows/wf-1");

    expect(await screen.findByText("Create the API")).toBeInTheDocument();
    expect(screen.getByText("app/main.py, app/models.py")).toBeInTheDocument();

    const integration = screen.getByRole("region", { name: "Integration" });
    const output = await within(integration).findByText("<script>alert(1)</script> 1 failed");
    expect(output.tagName).toBe("PRE");
    expect(integration.querySelector("script")).toBeNull();

    // BLOCKED + ADMIN: the partial package action is offered, behind a confirmation.
    await userEvent.click(screen.getByRole("button", { name: "Package current state (partial)" }));
    expect(await screen.findByRole("alertdialog")).toHaveTextContent("labelled PARTIAL unless");
  });

  it("hides the package action from members", async () => {
    stubApi(PROGRESS);
    renderWith(<ProjectDetailView />, "MEMBER");
    expect(await screen.findByRole("heading", { level: 1, name: "Todo API" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Package current state (partial)" })).toBeNull();
    expect(screen.queryByText(/waiting for a person/)).toBeNull();
    expect(screen.queryByRole("button", { name: /delete/i })).toBeNull();
  });

  it("lets admins cancel and delete an active project, then returns to the list", async () => {
    const fetchMock = stubApi(PROGRESS);
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url, init) => {
      const path = new URL(url).pathname;
      if (path.endsWith("/transitions")) return json(200, { ...WORKFLOW, status: "CANCELLED", revision: 5 });
      if (init?.method === "DELETE") return new Response(null, { status: 204 });
      return base(url, init);
    });
    renderWith(<ProjectDetailView />, "ADMIN");

    // BLOCKED is still at work, so the action cancels first.
    await userEvent.click(await screen.findByRole("button", { name: "Cancel & delete" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("cancels its work, then deletes it");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel & delete" }));

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/w/ws-1/projects"));
    const calls = fetchMock.mock.calls.filter(([, init]) => init?.method && init.method !== "GET");
    expect(calls.map(([url, init]) => [init?.method, new URL(url).pathname + new URL(url).search])).toEqual([
      ["POST", "/api/v1/workspaces/ws-1/workflows/wf-1/transitions"],
      ["DELETE", "/api/v1/workspaces/ws-1/workflows/wf-1?revision=5"],
    ]);
    expect(JSON.parse(String(calls[0][1]?.body))).toEqual({ to_status: "CANCELLED", revision: 4 });
  });
});

describe("change requests", () => {
  const completed: Workflow = { ...WORKFLOW, status: "COMPLETED" };

  function stub(workflow: Workflow) {
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async (url, init) => {
      const path = new URL(url).pathname;
      if (init?.method === "POST" && path.endsWith("/changes")) {
        return json(201, { ...completed, id: "wf-2", status: "PENDING", base_workflow_id: "wf-1" });
      }
      if (path.endsWith("/workflows/wf-1")) return json(200, workflow);
      if (path.endsWith("/progress")) return json(200, { ...PROGRESS, phase: "COMPLETED", percent: 100 });
      if (path.endsWith("/tasks")) return json(200, { items: [], total: 0, limit: 100, offset: 0 });
      if (path.endsWith("/integrations") || path.endsWith("/packages")) return json(200, []);
      return json(404, {});
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("starts a change project from a completed project, with an idempotency key", async () => {
    const fetchMock = stub(completed);
    renderWith(<ProjectDetailView />, "MEMBER");
    const region = await screen.findByRole("region", { name: "Request a change" });
    await userEvent.click(within(region).getByRole("button", { name: "Request change" }));
    expect(within(region).getByText("Describe the change first.")).toBeInTheDocument();

    await userEvent.type(within(region).getByLabelText("What should change?"), "Add a checkout page");
    await userEvent.click(within(region).getByRole("button", { name: "Request change" }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/w/ws-1/projects/wf-2"));
    const [url, init] = fetchMock.mock.calls.find(([, i]) => i?.method === "POST")!;
    expect(new URL(url).pathname).toBe("/api/v1/workspaces/ws-1/workflows/wf-1/changes");
    expect(JSON.parse(String(init?.body))).toEqual({ prompt: "Add a checkout page" });
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBeTruthy();
  });

  it("is offered only on completed projects and links a change to its base", async () => {
    stub({ ...WORKFLOW, base_workflow_id: "wf-0", base_head_sha: "0123456789abcdef" });
    renderWith(<ProjectDetailView />, "MEMBER");
    expect(await screen.findByRole("heading", { level: 1, name: "Todo API" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Request a change" })).toBeNull();
    expect(screen.getByRole("link", { name: "the previous project" })).toHaveAttribute(
      "href",
      "/w/ws-1/projects/wf-0",
    );
    expect(screen.getByText("0123456789")).toBeInTheDocument();
  });
});

describe("UiPreview", () => {
  const run = (id: string, previewId: string | null, outcome: "passed" | "failed" = "passed") => ({
    id,
    workflow_id: "wf-1",
    head_sha: "a".repeat(40),
    stack_profile: "python-3.12-web",
    status: outcome === "passed" ? ("PASSED" as const) : ("FAILED" as const),
    checks: [
      { name: "tests", outcome: "passed" as const },
      { name: "ui", outcome, preview_artifact_id: previewId },
    ],
    error_code: null,
    created_at: "2026-11-27T10:00:00Z",
    finished_at: "2026-11-27T10:00:30Z",
  });

  it("shows the newest screenshot from the artifacts API, and nothing without one", () => {
    expect(latestPreview([run("r-1", null)])).toBeNull();
    renderWith(<UiPreview workflowId="wf-1" runs={[run("r-2", "img-2", "failed"), run("r-1", "img-1")]} />);
    const image = screen.getByRole("img", { name: /Screenshot of the project's web page/ });
    expect(image).toHaveAttribute(
      "src",
      "http://localhost:8000/api/v1/workspaces/ws-1/workflows/wf-1/artifacts/img-2/download",
    );
    expect(screen.getByText(/UI checks did not pass yet/)).toBeInTheDocument();
  });
});
