import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Workflow, WorkflowCosts } from "@/lib/api/types";
import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { BudgetSpent, CostSummary } from "./cost-summary";

const COSTS: WorkflowCosts = {
  calls: 5,
  calls_by_status: { SUCCEEDED: 4, INVALID_OUTPUT: 1 },
  unpriced_calls: 1,
  input_tokens: 4000,
  output_tokens: 2500,
  cached_input_tokens: 800,
  totals: [{ currency: "USD", actual: "0.00840000", estimated: "0.00400000", mock: "0E-8" }],
  budget: { limit: "1.0000", currency: "USD", comparable: true, counted_spend: "0.01240000", reached: false },
  checked_calls: 4,
  mismatched_calls: 0,
  reconciled: true,
};

function renderWith(ui: React.ReactNode, costs: WorkflowCosts) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify(costs), { status: 200, headers: { "Content-Type": "application/json" } }),
    ),
  );
  render(
    <QueryClientProvider client={createQueryClient()}>
      <WorkspaceProvider value={{ id: "ws-1", name: "W", role: "VIEWER" }}>{ui}</WorkspaceProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("CostSummary", () => {
  it("shows actual, estimated and mock costs apart, with calls, tokens and reconciliation", async () => {
    renderWith(<CostSummary workflowId="wf-1" />, COSTS);
    const region = await screen.findByRole("region", { name: "Model costs" });
    await vi.waitFor(() => expect(region).toHaveTextContent("Actual (USD)$0.0084"));
    expect(region).toHaveTextContent("Estimated (USD)$0.004");
    expect(region).toHaveTextContent("Mock provider (USD), not a real charge$0.00");
    expect(region).toHaveTextContent("Model calls5 (4 succeeded, 1 invalid output)");
    expect(region).toHaveTextContent("Unpriced calls1 without a price or reported usage");
    expect(region).toHaveTextContent("Tokens4,000 in · 2,500 out · 800 cached");
    expect(region).toHaveTextContent("Reconciled: all 4 priced calls match their price snapshot.");
  });

  it("says when stored costs no longer match their prices", async () => {
    renderWith(<CostSummary workflowId="wf-1" />, { ...COSTS, mismatched_calls: 1, reconciled: false });
    expect(
      await screen.findByText("Not reconciled: 1 of 4 priced calls do not match their price snapshot."),
    ).toBeInTheDocument();
  });

  it("says when nothing was called yet", async () => {
    renderWith(<CostSummary workflowId="wf-1" />, { ...COSTS, calls: 0, totals: [] });
    expect(await screen.findByText("No model calls yet.")).toBeInTheDocument();
  });
});

describe("BudgetSpent", () => {
  const workflow = { id: "wf-1" } as Workflow;

  it("compares counted spend with the budget", async () => {
    renderWith(<BudgetSpent workflow={workflow} />, {
      ...COSTS,
      budget: { ...COSTS.budget!, counted_spend: "1.20000000", reached: true },
    });
    expect(await screen.findByText("$1.20 of $1.00 (reached)")).toBeInTheDocument();
  });

  it("never converts currencies", async () => {
    renderWith(<BudgetSpent workflow={workflow} />, {
      ...COSTS,
      budget: { ...COSTS.budget!, currency: "EUR", comparable: false, counted_spend: "0" },
    });
    expect(
      await screen.findByText("Not comparable (EUR budget, prices in USD); not enforced"),
    ).toBeInTheDocument();
  });

  it("says when no budget is set", async () => {
    renderWith(<BudgetSpent workflow={workflow} />, { ...COSTS, budget: null });
    expect(await screen.findByText("No budget set")).toBeInTheDocument();
  });
});
