import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Workflow } from "@/lib/api/types";
import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { PauseBanner } from "./pause-banner";
import { WorkflowActions } from "./workflow-actions";

vi.mock("@/lib/auth/auth-provider", () => ({ useAuth: () => ({ me: { user: { id: "u-me" } } }) }));

const WORKFLOW = {
  id: "wf-1",
  status: "PAUSED",
  autonomy_policy: "approval_required",
  revision: 4,
  pause_reason: "USER",
  paused_by_user_id: "u-other",
  paused_at: new Date().toISOString(),
  pause_note: "<i>Waiting for legal</i>",
} as Workflow;

afterEach(() => vi.unstubAllGlobals());

describe("PauseBanner", () => {
  it("says who paused and shows their note as plain text", () => {
    render(<PauseBanner workflow={WORKFLOW} />);
    const banner = screen.getByRole("region", { name: "Paused" });
    expect(banner).toHaveTextContent("Paused by a member");
    expect(banner).toHaveTextContent("Reason: <i>Waiting for legal</i>");
    expect(banner.querySelector("i")).toBeNull();
  });

  it("recognises the viewer's own pause", () => {
    render(<PauseBanner workflow={{ ...WORKFLOW, paused_by_user_id: "u-me" }} />);
    expect(screen.getByRole("region", { name: "Paused" })).toHaveTextContent("Paused by you");
  });

  it("explains the platform's pauses, without a person's note", () => {
    const { rerender } = render(
      <PauseBanner workflow={{ ...WORKFLOW, pause_reason: "AWAITING_DECISION", paused_by_user_id: null }} />,
    );
    const banner = screen.getByRole("region", { name: "Paused" });
    expect(banner).toHaveTextContent("Paused for a decision");
    expect(banner).toHaveTextContent("It resumes by itself once every such request is decided.");
    expect(banner).not.toHaveTextContent("Reason:");

    rerender(<PauseBanner workflow={{ ...WORKFLOW, pause_reason: "DEADLINE_PASSED", paused_by_user_id: null }} />);
    expect(screen.getByRole("region", { name: "Paused" })).toHaveTextContent("Paused at its deadline");
  });

  it("shows nothing unless the workflow is paused", () => {
    render(<PauseBanner workflow={{ ...WORKFLOW, status: "RUNNING", pause_reason: null }} />);
    expect(screen.queryByRole("region", { name: "Paused" })).not.toBeInTheDocument();
  });
});

describe("Pausing", () => {
  it("asks for confirmation and sends the optional reason", async () => {
    document.cookie = "agentflow_csrf=t-1";
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () =>
        new Response(JSON.stringify({ ...WORKFLOW, revision: 5 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <QueryClientProvider client={createQueryClient()}>
        <WorkspaceProvider value={{ id: "ws-1", name: "W", role: "MEMBER" }}>
          <WorkflowActions workflow={{ ...WORKFLOW, status: "RUNNING", pause_reason: null }} onError={() => {}} />
        </WorkspaceProvider>
      </QueryClientProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    const dialog = screen.getByRole("alertdialog");
    await userEvent.click(within(dialog).getByLabelText("Reason (optional)"));
    await userEvent.paste("  Waiting for legal  ");
    await userEvent.click(within(dialog).getByRole("button", { name: "Pause" }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toMatch(/\/workflows\/wf-1\/transitions$/);
    expect(JSON.parse(String(init?.body))).toEqual({
      to_status: "PAUSED",
      revision: 4,
      reason: "Waiting for legal",
    });
  });
});
