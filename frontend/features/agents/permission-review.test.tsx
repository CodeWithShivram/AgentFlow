import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentConfiguration } from "@/lib/api/types";
import { createQueryClient } from "@/lib/query-client";

import { PermissionReview } from "./permission-review";

const CATALOG = {
  capabilities: ["research"],
  categories: ["custom"],
  tools: [
    { name: "document_retrieval", risk: "LOW", description: "d", available_in_phase_1: true, usable_now: false },
    { name: "web_search", risk: "MEDIUM", description: "d", available_in_phase_1: true, usable_now: true },
    { name: "python_analysis", risk: "HIGH", description: "d", available_in_phase_1: false, usable_now: false },
  ],
};

function configuration(tools: AgentConfiguration["tools"]): AgentConfiguration {
  return { capabilities: ["research"], tools } as unknown as AgentConfiguration;
}

function renderReview(config: AgentConfiguration, isOwner: boolean) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(CATALOG), { status: 200, headers: { "Content-Type": "application/json" } })),
  );
  render(
    <QueryClientProvider client={createQueryClient()}>
      <PermissionReview configuration={config} isOwner={isOwner} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("PermissionReview", () => {
  it("lists each requested tool with its risk, usability and owner requirement", async () => {
    renderReview(
      configuration([
        { tool: "document_retrieval", access: "read" },
        { tool: "web_search", access: "read" },
        { tool: "python_analysis", access: "write" },
      ]),
      false,
    );

    const review = screen.getByRole("region", { name: "Permission review" });
    const items = await within(review).findAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Document retrieval");
    expect(items[0]).toHaveTextContent("Low risk");
    expect(items[0]).toHaveTextContent("Not usable yet: grants a permission only");
    expect(items[1]).toHaveTextContent("Medium risk");
    expect(items[1]).toHaveTextContent("Usable now");
    expect(items[2]).toHaveTextContent("High risk");
    expect(items[2]).toHaveTextContent("write access");
    expect(items[2]).toHaveTextContent("Approving needs the workspace owner (not you).");
  });

  it("says when a proposal requests no tools", () => {
    renderReview(configuration([]), true);
    expect(screen.getByRole("region", { name: "Permission review" })).toHaveTextContent("Requests no tools.");
  });
});
