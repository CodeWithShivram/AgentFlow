import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { ProposalsView } from "./proposals-view";

let search = new URLSearchParams();
vi.mock("next/navigation", () => ({
  usePathname: () => "/w/ws-1/proposals",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => search,
}));
vi.mock("@/lib/auth/auth-provider", () => ({ useAuth: () => ({ me: null }) }));

const EMPTY = { items: [], total: 0, limit: 20, offset: 0 };

afterEach(() => {
  vi.unstubAllGlobals();
  search = new URLSearchParams();
});

describe("ProposalsView", () => {
  it("keeps loading instead of showing the previous filter's empty page", async () => {
    let answerPending: (() => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        const json = () =>
          new Response(JSON.stringify(EMPTY), { status: 200, headers: { "Content-Type": "application/json" } });
        if (new URL(input, "http://localhost").searchParams.get("status") === "PENDING") {
          return new Promise<Response>((resolve) => {
            answerPending = () => resolve(json());
          });
        }
        return json();
      }),
    );
    const ui = () => (
      <QueryClientProvider client={client}>
        <WorkspaceProvider value={{ id: "ws-1", name: "W", role: "MEMBER" }}>
          <ProposalsView />
        </WorkspaceProvider>
      </QueryClientProvider>
    );
    const client = createQueryClient();
    const { rerender } = render(ui());
    expect(await screen.findByText("No proposals")).toBeInTheDocument();

    // As after submitting a proposal: the Pending filter is still loading.
    search = new URLSearchParams("status=PENDING");
    rerender(ui());
    expect(await screen.findByRole("status", { name: "Loading proposals" })).toBeInTheDocument();
    expect(screen.queryByText("No proposals")).not.toBeInTheDocument();

    answerPending!();
    expect(await screen.findByText("No proposals")).toBeInTheDocument();
  });
});
