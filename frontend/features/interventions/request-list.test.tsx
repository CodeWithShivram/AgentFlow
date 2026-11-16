import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NeedsInput } from "@/features/workflows/needs-input";
import type { Intervention, WorkspaceRole } from "@/lib/api/types";
import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { RequestList } from "./request-list";

const BASE: Intervention = {
  id: "i-1",
  workflow_id: "wf-1",
  task_id: null,
  type: "CLARIFICATION",
  subject: "analysis_question",
  status: "OPEN",
  requested_action: "Which region should prices use?",
  context: { impact: "Prices vary by region.", ambiguity_id: "a1" },
  requested_by: "SYSTEM",
  requested_by_agent_id: null,
  resolved_by_user_id: null,
  resolution: null,
  resolved_at: null,
  expires_at: null,
  created_at: new Date().toISOString(),
  workflow_prompt: "Plan a product launch",
};

const AGENT_ACTION: Intervention = {
  ...BASE,
  id: "i-2",
  task_id: "t-1",
  type: "APPROVAL",
  subject: "agent_action",
  requested_action: "<b>Email the customer list</b>",
  context: {
    reason: "The brief asks for outreach.",
    risks: ["Customers may be contacted twice."],
    consequences: ["Emails cannot be recalled."],
    if_approved: "A new attempt by the same agent, told the action is approved.",
    if_rejected: "A new attempt by the same agent, told to finish without that action.",
  },
  requested_by: "AGENT",
  requested_by_agent_id: "ag-1",
};

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

function renderWith(role: WorkspaceRole, ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <WorkspaceProvider value={{ id: "ws-1", name: "W", role }}>{ui}</WorkspaceProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("RequestList", () => {
  it("lets members answer a question and sends the trimmed answer", async () => {
    document.cookie = "agentflow_csrf=t-1";
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(async () =>
      json({ ...BASE, status: "RESOLVED" }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWith("MEMBER", <RequestList items={[BASE]} showWorkflow />);

    const item = screen.getByRole("article", { name: "Question about the request" });
    expect(item).toHaveTextContent("Which region should prices use?");
    expect(item).toHaveTextContent("Why it matters: Prices vary by region.");
    expect(within(item).getByRole("link", { name: "Plan a product launch" })).toHaveAttribute(
      "href",
      "/w/ws-1/workflows/wf-1",
    );
    // Pasted (one input event) rather than typed key by key, which is slow under a loaded gate.
    await userEvent.click(within(item).getByLabelText("Your answer"));
    await userEvent.paste("  Europe only  ");
    await userEvent.click(within(item).getByRole("button", { name: "Send answer" }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toMatch(/\/workspaces\/ws-1\/interventions\/i-1\/answer$/);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ answer: "Europe only" });
  });

  it("does not let viewers answer", () => {
    renderWith("VIEWER", <RequestList items={[BASE]} showWorkflow={false} />);
    expect(screen.queryByLabelText("Your answer")).not.toBeInTheDocument();
    expect(screen.getByText("A member can answer this question.")).toBeInTheDocument();
  });

  it("shows an agent's request as plain text, with what each decision leads to", () => {
    renderWith("MEMBER", <RequestList items={[AGENT_ACTION]} showWorkflow={false} />);
    const item = screen.getByRole("article", { name: "Agent asks for approval" });
    // Agent text is never interpreted as markup.
    expect(item).toHaveTextContent("<b>Email the customer list</b>");
    expect(item.querySelector("b")).toBeNull();
    expect(item).toHaveTextContent("Customers may be contacted twice.");
    expect(item).toHaveTextContent("If rejected: A new attempt by the same agent");
    expect(within(item).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(item).toHaveTextContent("An admin approves or rejects this request.");
  });

  it("lets admins approve", async () => {
    document.cookie = "agentflow_csrf=t-1";
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(async () =>
      json({ ...AGENT_ACTION, status: "RESOLVED", resolution: { decision: "approve" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWith("ADMIN", <RequestList items={[AGENT_ACTION]} showWorkflow={false} />);

    await userEvent.click(screen.getByRole("button", { name: "Approve" }));

    await vi.waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => url.endsWith("/interventions/i-2/approve") && init?.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it("shows how a closed request ended", () => {
    renderWith("MEMBER", (
      <RequestList
        items={[
          { ...BASE, status: "RESOLVED", resolution: { answer: "Europe only" }, resolved_at: BASE.created_at },
          { ...AGENT_ACTION, status: "WITHDRAWN", resolution: { outcome: "CANCELLED" } },
        ]}
        showWorkflow={false}
      />
    ));
    expect(screen.getByRole("article", { name: "Question about the request" })).toHaveTextContent(
      "Answer: Europe only",
    );
    expect(screen.getByRole("article", { name: "Agent asks for approval" })).toHaveTextContent(
      "Withdrawn: Cancelled",
    );
    expect(screen.queryByRole("button", { name: "Send answer" })).not.toBeInTheDocument();
  });
});

describe("NeedsInput", () => {
  it("lists the workflow's open requests and hides itself when there are none", async () => {
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(async () =>
      json({ items: [BASE], total: 1, limit: 100, offset: 0 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWith("MEMBER", <NeedsInput workflowId="wf-1" version="1:1" />);

    const region = await screen.findByRole("region", { name: "Needs your input" });
    expect(region).toHaveTextContent("Needs your input (1)");
    const url = String(fetchMock.mock.calls[0]![0]);
    expect(url).toContain("workflow_id=wf-1");
    expect(url).toContain("status=OPEN");
  });

  it("stays on screen while a refresh loads, so the page below does not shift", async () => {
    let release: (() => void) | undefined;
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(async () => {
      if (fetchMock.mock.calls.length > 1) await new Promise<void>((resolve) => (release = resolve));
      return json({ items: [BASE], total: 1, limit: 100, offset: 0 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = createQueryClient();
    const ui = (version: string) => (
      <QueryClientProvider client={client}>
        <WorkspaceProvider value={{ id: "ws-1", name: "W", role: "MEMBER" }}>
          <NeedsInput workflowId="wf-1" version={version} />
        </WorkspaceProvider>
      </QueryClientProvider>
    );
    const { rerender } = render(ui("1:1"));
    await screen.findByRole("region", { name: "Needs your input" });

    rerender(ui("1:2")); // a new event: refetch under a new key
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("region", { name: "Needs your input" })).toBeInTheDocument();
    release?.();
  });

  it("renders nothing without open requests", async () => {
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(async () =>
      json({ items: [], total: 0, limit: 100, offset: 0 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWith("MEMBER", <NeedsInput workflowId="wf-1" version="1:1" />);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByRole("region", { name: "Needs your input" })).not.toBeInTheDocument();
  });
});
