import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnalyticsOverview, CostBreakdown } from "@/lib/api/types";
import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { AnalyticsView } from "./analytics-view";

let search = new URLSearchParams();
vi.mock("next/navigation", () => ({
  usePathname: () => "/w/ws-1/analytics",
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => search,
}));

const RANGE = { start: "2026-10-20T00:00:00Z", end: "2026-11-19T00:00:00Z" };
const USD = { currency: "USD", actual: "0.00900000", estimated: "0.00400000", mock: "1.50000000" };
const TOTALS = {
  calls: 5,
  unpriced_calls: 1,
  input_tokens: 5000,
  output_tokens: 3000,
  cached_input_tokens: 0,
  costs: [USD],
};
const OVERVIEW: AnalyticsOverview = { range: RANGE, totals: TOTALS, workflows: 2, agents: 2 };

function breakdown(groupBy: string): CostBreakdown {
  const rows = {
    workflow: [{ ...TOTALS, calls: 3, key: "wf-1", label: "<b>Write the report</b>" }],
    agent: [
      { ...TOTALS, calls: 1, key: "ag-1", label: "Writer" },
      { ...TOTALS, calls: 2, key: "platform:plan_generation", label: "Platform: plan_generation", costs: [] },
    ],
    model: [{ ...TOTALS, calls: 2, key: "acme/acme-large", label: "acme · acme-large" }],
  }[groupBy as "workflow" | "agent" | "model"];
  return { group_by: groupBy as CostBreakdown["group_by"], range: RANGE, rows, totals: TOTALS, truncated: groupBy === "model" };
}

function renderView(overview: AnalyticsOverview = OVERVIEW) {
  const fetchMock = vi.fn<(input: string) => Promise<Response>>(async (input) => {
    const url = new URL(input, "http://localhost");
    const body = url.pathname.endsWith("/overview") ? overview : breakdown(url.searchParams.get("group_by") ?? "");
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider client={createQueryClient()}>
      <WorkspaceProvider value={{ id: "ws-1", name: "W", role: "VIEWER" }}>
        <AnalyticsView />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  search = new URLSearchParams();
});

describe("AnalyticsView", () => {
  it("shows the overview with actual, estimated and mock costs apart", async () => {
    renderView();
    const overview = await screen.findByRole("region", { name: "Overview" });
    await vi.waitFor(() => expect(overview).toHaveTextContent("Model calls5"));
    expect(overview).toHaveTextContent("Workflows with model calls2");
    expect(overview).toHaveTextContent("Tokens5,000 in · 3,000 out · 0 cached");
    expect(overview).toHaveTextContent("Actual (USD)$0.009");
    expect(overview).toHaveTextContent("Estimated (USD)$0.004");
    expect(overview).toHaveTextContent("Mock provider (USD), not a real charge$1.50");
    expect(overview).toHaveTextContent("Unpriced calls1");
  });

  it("breaks usage down by workflow, agent and model, as plain text", async () => {
    renderView();
    const byWorkflow = await screen.findByRole("region", { name: "By workflow" });
    const link = await within(byWorkflow).findByRole("link", { name: "<b>Write the report</b>" });
    expect(link).toHaveAttribute("href", "/w/ws-1/workflows/wf-1");
    expect(byWorkflow.querySelector("b")).toBeNull();

    const byAgent = screen.getByRole("region", { name: "By agent" });
    expect(await within(byAgent).findByText("Platform: plan_generation")).toBeInTheDocument();
    // An unpriced group shows no cost rather than zero.
    const platformRow = within(byAgent).getByText("Platform: plan_generation").closest("tr")!;
    expect(platformRow).toHaveTextContent("—");

    const byModel = screen.getByRole("region", { name: "By model" });
    expect(await within(byModel).findByText("Showing the groups with the most calls.")).toBeInTheDocument();
  });

  it("asks for the chosen range", async () => {
    search = new URLSearchParams("days=7");
    const fetchMock = renderView();
    await screen.findByRole("region", { name: "Overview" });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const url = new URL(String(fetchMock.mock.calls[0]![0]), "http://localhost");
    const from = Date.parse(url.searchParams.get("from")!);
    const to = Date.parse(url.searchParams.get("to")!);
    expect(Math.round((to - from) / 86_400_000)).toBe(7);
    expect(screen.getByRole("tab", { name: "Last 7 days" })).toHaveAttribute("aria-selected", "true");
  });

  it("says when nothing was called in the range", async () => {
    renderView({ ...OVERVIEW, totals: { ...TOTALS, calls: 0, costs: [] }, workflows: 0, agents: 0 });
    const overview = await screen.findByRole("region", { name: "Overview" });
    expect(await within(overview).findByText("No model calls in this range.")).toBeInTheDocument();
  });
});
