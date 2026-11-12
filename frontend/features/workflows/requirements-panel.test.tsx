import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PromptAnalysis, Workflow, WorkspaceRole } from "@/lib/api/types";
import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { RequirementsPanel } from "./requirements-panel";

const ANALYSIS: PromptAnalysis = {
  schema_version: 1,
  objective: "Compare three CRM vendors",
  requirements: [
    { id: "r1", description: "Cover pricing", priority: "must" },
    { id: "r2", description: "Mention support quality", priority: "should" },
  ],
  constraints: [{ id: "c1", kind: "budget", description: "Under 500 EUR" }],
  deliverables: ["Comparison table"],
  assumptions: [],
  ambiguities: [{ id: "a1", question: "Which region?", impact: "Prices vary by region." }],
  suggested_skills: ["research", "report_writing"],
};

function workflow(overrides: Partial<Workflow>): Workflow {
  return {
    id: "wf-1",
    status: "PLANNING",
    analysis_status: "READY",
    normalized_requirements: ANALYSIS,
    ...overrides,
  } as Workflow;
}

function renderPanel(wf: Workflow, role: WorkspaceRole = "MEMBER") {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <WorkspaceProvider value={{ id: "ws-1", name: "W", role }}>
        <RequirementsPanel workflow={wf} />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("RequirementsPanel", () => {
  it("shows the analysis as text, with priorities and open questions", () => {
    renderPanel(workflow({}));
    expect(screen.getByText("Compare three CRM vendors")).toBeInTheDocument();
    expect(screen.getByText("Must")).toBeInTheDocument();
    expect(screen.getByText("Should")).toBeInTheDocument();
    expect(screen.getByText("Open questions (1)")).toBeInTheDocument();
    expect(screen.getByText("Which region?")).toBeInTheDocument();
    expect(screen.getByText("Under 500 EUR")).toBeInTheDocument();
    expect(screen.getByText("No assumptions recorded.")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Suggested skills" })).toHaveTextContent("Report writing");
  });

  it("never renders model output as HTML", () => {
    const hostile = { ...ANALYSIS, objective: "<img src=x onerror=alert(1)>" };
    const { container } = renderPanel(workflow({ normalized_requirements: hostile }));
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
  });

  it("announces a pending analysis", () => {
    renderPanel(workflow({ analysis_status: "PENDING", normalized_requirements: null }));
    expect(screen.getByRole("status")).toHaveTextContent("Analysing the request");
  });

  it("lets members retry a failed analysis", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ ...workflow({}), analysis_status: "PENDING" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    document.cookie = "agentflow_csrf=t-1";
    renderPanel(workflow({ analysis_status: "FAILED", normalized_requirements: null }));

    await userEvent.click(screen.getByRole("button", { name: "Retry analysis" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/workspaces\/ws-1\/workflows\/wf-1\/analysis$/);
    expect(init.method).toBe("POST");
  });

  it("offers no retry to viewers", () => {
    renderPanel(workflow({ analysis_status: "FAILED", normalized_requirements: null }), "VIEWER");
    expect(screen.getByText("The analysis failed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry analysis" })).not.toBeInTheDocument();
  });
});
