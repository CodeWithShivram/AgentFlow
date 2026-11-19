import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnalyticsOverview, CostBreakdown, Distribution, WorkspaceMetrics } from "@/lib/api/types";
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

const NONE: Distribution = { unit: "s", samples: 0, p50: null, p95: null, max: null, mean: null };
const NO_RATE = { numerator: 0, denominator: 0, value: null };
const METRICS: WorkspaceMetrics = {
  range: RANGE,
  model_calls: {
    calls: 4,
    by_status: { SUCCEEDED: 3, FAILED: 1 },
    failure_rate: { numerator: 1, denominator: 4, value: 0.25 },
    latency: { unit: "ms", samples: 4, p50: 250, p95: 385, max: 400, mean: 250 },
    by_model: [],
  },
  attempts: {
    finished: 4,
    by_status: { SUCCEEDED: 2, FAILED: 1, CANCELLED: 1 },
    in_flight: 1,
    failure_rate: { numerator: 1, denominator: 3, value: 0.333333 },
    retry_rate: { numerator: 1, denominator: 4, value: 0.25 },
    duration: { unit: "s", samples: 4, p50: 15, p95: 28.5, max: 30, mean: 16.25 },
    by_agent: [
      {
        key: "ag-1",
        label: "Writer",
        attempts: 2,
        failure_rate: { numerator: 1, denominator: 2, value: 0.5 },
        retry_rate: { numerator: 1, denominator: 2, value: 0.5 },
        duration: NONE,
      },
    ],
  },
  tasks: { completed: 2, first_attempt: { numerator: 1, denominator: 2, value: 0.5 } },
  verification: { deterministic_pass_rate: NO_RATE, semantic_pass_rate: NO_RATE },
  tools: { calls: 0, by_status: {}, failure_rate: NO_RATE, denial_rate: NO_RATE, duration: NONE, by_tool: [] },
  workflows: { finished: 0, by_status: {}, in_flight: 1, duration: NONE },
};

function renderView(overview: AnalyticsOverview = OVERVIEW) {
  const fetchMock = vi.fn<(input: string) => Promise<Response>>(async (input) => {
    const url = new URL(input, "http://localhost");
    const body = url.pathname.endsWith("/overview")
      ? overview
      : url.pathname.endsWith("/metrics")
        ? METRICS
        : breakdown(url.searchParams.get("group_by") ?? "");
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

  it("shows rates with their counts, percentiles with their samples, and unmeasured as such", async () => {
    renderView();
    const section = await screen.findByRole("region", { name: "Reliability and latency" });
    const attempts = await within(section).findByRole("region", { name: "Agent attempts" });
    expect(attempts).toHaveTextContent("Failure rate (cancelled excluded)33.3% (1 of 3)");
    expect(attempts).toHaveTextContent("Retry rate25% (1 of 4)");
    expect(attempts).toHaveTextContent("Durationmedian 15.0 s · 95th 28.5 s · max 30.0 s (4)");
    expect(attempts).toHaveTextContent("Tasks accepted on the first attempt50% (1 of 2)");
    expect(attempts).toHaveTextContent("Semantic evaluations passedNot measured");
    expect(within(attempts).getByRole("row", { name: /Writer/ })).toHaveTextContent("50% (1 of 2)");
    const calls = within(section).getByRole("region", { name: "Model calls" });
    expect(calls).toHaveTextContent("Latencymedian 250 ms · 95th 385 ms · max 400 ms (4)");
    expect(within(section).getByRole("region", { name: "Tool calls" })).toHaveTextContent("Failure rateNot measured");
    expect(within(section).getByRole("region", { name: "Workflows" })).toHaveTextContent("Run timeNot measured");
  });

  it("says when nothing was called in the range", async () => {
    renderView({ ...OVERVIEW, totals: { ...TOTALS, calls: 0, costs: [] }, workflows: 0, agents: 0 });
    const overview = await screen.findByRole("region", { name: "Overview" });
    expect(await within(overview).findByText("No model calls in this range.")).toBeInTheDocument();
  });
});
