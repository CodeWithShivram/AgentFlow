import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnalyticsOverview, Distribution, MetricRate, WorkspaceMetrics } from "@/lib/api/types";
import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { UsageSummary } from "./usage-summary";

const RANGE = { start: "2026-10-20T00:00:00Z", end: "2026-11-19T00:00:00Z" };
const NONE: Distribution = { unit: "s", samples: 0, p50: null, p95: null, max: null, mean: null };
const NO_RATE: MetricRate = { numerator: 0, denominator: 0, value: null };
const TOTALS = {
  calls: 5,
  unpriced_calls: 1,
  input_tokens: 5000,
  output_tokens: 3000,
  cached_input_tokens: 0,
  costs: [{ currency: "USD", actual: "0.00900000", estimated: "0.00400000", mock: "1.50000000" }],
};

function metrics(byStatus: Record<string, number>, failureRate = NO_RATE): WorkspaceMetrics {
  return {
    range: RANGE,
    model_calls: { calls: 0, by_status: {}, failure_rate: NO_RATE, latency: { ...NONE, unit: "ms" }, by_model: [] },
    attempts: {
      finished: 0,
      by_status: byStatus,
      in_flight: 0,
      failure_rate: failureRate,
      retry_rate: NO_RATE,
      duration: NONE,
      by_agent: [],
    },
    tasks: { completed: 0, first_attempt: NO_RATE },
    verification: { deterministic_pass_rate: NO_RATE, semantic_pass_rate: NO_RATE },
    tools: { calls: 0, by_status: {}, failure_rate: NO_RATE, denial_rate: NO_RATE, duration: NONE, by_tool: [] },
    workflows: { finished: 0, by_status: {}, in_flight: 0, duration: NONE },
  };
}

function renderSummary(overview: AnalyticsOverview, workspaceMetrics: WorkspaceMetrics) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      const body = new URL(input, "http://localhost").pathname.endsWith("/overview") ? overview : workspaceMetrics;
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
  render(
    <QueryClientProvider client={createQueryClient()}>
      <WorkspaceProvider value={{ id: "ws-1", name: "W", role: "VIEWER" }}>
        <UsageSummary />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("UsageSummary", () => {
  it("summarises the last 30 days with costs by basis and the attempt success rate", async () => {
    renderSummary(
      { range: RANGE, totals: TOTALS, workflows: 2, agents: 2 },
      metrics({ SUCCEEDED: 3, FAILED: 1, CANCELLED: 1 }, { numerator: 1, denominator: 4, value: 0.25 }),
    );
    const card = await screen.findByRole("region", { name: "Usage, last 30 days" });
    await vi.waitFor(() => expect(card).toHaveTextContent("Model calls5"));
    expect(card).toHaveTextContent("Tokens5,000 in · 3,000 out");
    expect(card).toHaveTextContent("Actual (USD)$0.009");
    expect(card).toHaveTextContent("Estimated (USD)$0.004");
    expect(card).toHaveTextContent("Mock (USD), not a real charge$1.50");
    expect(card).toHaveTextContent("Unpriced calls1");
    expect(card).toHaveTextContent("Agent attempts succeeded75% (3 of 4)");
    expect(screen.getByRole("link", { name: "Analytics" })).toHaveAttribute("href", "/w/ws-1/analytics");
  });

  it("says when nothing was called, and never shows an unmeasured rate as 0%", async () => {
    renderSummary(
      { range: RANGE, totals: { ...TOTALS, calls: 0, unpriced_calls: 0, costs: [] }, workflows: 0, agents: 0 },
      metrics({}),
    );
    const card = await screen.findByRole("region", { name: "Usage, last 30 days" });
    await vi.waitFor(() => expect(card).toHaveTextContent("Model callsNo model calls yet"));
    expect(card).toHaveTextContent("Agent attempts succeededNot measured");
    expect(card).not.toHaveTextContent("0%");
  });
});
