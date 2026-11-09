import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ErrorState } from "@/components/common/states";
import { StatusBadge } from "@/components/common/status-badge";
import { ApiError } from "@/lib/api/errors";
import type { Workflow } from "@/lib/api/types";
import { AuthProvider, useAuth } from "@/lib/auth/auth-provider";
import { createQueryClient } from "@/lib/query-client";
import { WorkspaceProvider } from "@/lib/workspace";

import { agentFormDefaults, agentFormSchema, toConfiguration } from "./agents/agent-config-fields";
import { LoginView } from "./auth/login-view";
import { RegisterView } from "./auth/register-view";
import { WorkflowActions } from "./workflows/workflow-actions";

const router = { replace: vi.fn(), push: vi.fn() };
let search = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useSearchParams: () => search,
  usePathname: () => "/w/ws-1/dashboard",
  useParams: () => ({ workspaceId: "ws-1" }),
}));

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
const unauthenticated = () =>
  json(401, { error: { code: "AUTHENTICATION_REQUIRED", message: "Sign in.", request_id: null, details: {} } });
const me = { user: { id: "u1", email: "ada@example.com", created_at: "" }, memberships: [] };

function renderWithProviders(ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <AuthProvider>{ui}</AuthProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  search = new URLSearchParams();
  router.replace.mockReset();
  router.push.mockReset();
  document.cookie = "agentflow_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});
afterEach(() => vi.unstubAllGlobals());

describe("LoginView", () => {
  it("validates before calling the API", async () => {
    const fetchMock = vi.fn<(url: string) => Promise<Response>>(async () => unauthenticated());
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<LoginView />);
    await userEvent.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/auth/login"))).toHaveLength(0);
  });

  it("shows the API's message and keeps the typed password focused for correction", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.endsWith("/auth/login")
          ? json(401, { error: { code: "INVALID_CREDENTIALS", message: "Email or password is incorrect.", request_id: null, details: {} } })
          : unauthenticated(),
      ),
    );
    renderWithProviders(<LoginView />);
    await userEvent.type(screen.getByLabelText("Email"), "ada@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrong-password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
    expect(screen.getByLabelText("Password")).toHaveValue("wrong-password");
    expect(screen.getByLabelText("Password")).toHaveFocus();
  });

  it("signs in and goes to the requested page only if it is safe", async () => {
    search = new URLSearchParams({ next: "//evil.example" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.endsWith("/auth/login")
          ? json(200, {
              ...me,
              memberships: [{ workspace_id: "ws-9", workspace_name: "W", role: "OWNER" }],
              access_expires_at: "",
              session_expires_at: "",
            })
          : unauthenticated(),
      ),
    );
    renderWithProviders(<LoginView />);
    await userEvent.type(screen.getByLabelText("Email"), "ada@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "correct-password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/w/ws-9/dashboard"));
  });

  it("explains an expired session", async () => {
    search = new URLSearchParams({ expired: "1" });
    vi.stubGlobal("fetch", vi.fn(async () => unauthenticated()));
    renderWithProviders(<LoginView />);
    expect(await screen.findByText("Your session expired")).toBeInTheDocument();
  });
});

describe("RegisterView", () => {
  it("enforces the API's password rules and matching confirmation", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => unauthenticated()));
    renderWithProviders(<RegisterView />);
    await userEvent.type(screen.getByLabelText("Email"), "ada@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "short");
    await userEvent.type(screen.getByLabelText("Confirm password"), "different");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("Use at least 12 characters.")).toBeInTheDocument();
    expect(screen.getByText("The passwords do not match.")).toBeInTheDocument();
  });
});

describe("AuthProvider", () => {
  function Status() {
    const { status, sessionExpired } = useAuth();
    return <p>{`${status}:${String(sessionExpired)}`}</p>;
  }

  it("restores a session from cookies via /auth/me", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, me)));
    renderWithProviders(<Status />);
    expect(await screen.findByText("authenticated:false")).toBeInTheDocument();
  });

  it("is anonymous without a session and never calls refresh without a CSRF cookie", async () => {
    const fetchMock = vi.fn<(url: string) => Promise<Response>>(async () => unauthenticated());
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<Status />);
    expect(await screen.findByText("anonymous:false")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/auth/refresh"))).toBe(false);
  });
});

describe("WorkflowActions permissions", () => {
  const workflow = { id: "wf", status: "AWAITING_APPROVAL", autonomy_policy: "approval_required", revision: 2 } as Workflow;
  const renderAs = (role: "VIEWER" | "MEMBER" | "ADMIN") =>
    render(
      <QueryClientProvider client={createQueryClient()}>
        <WorkspaceProvider value={{ id: "ws-1", name: "W", role }}>
          <WorkflowActions workflow={workflow} onError={() => undefined} />
        </WorkspaceProvider>
      </QueryClientProvider>,
    );

  it("shows approval to admins only", () => {
    renderAs("ADMIN");
    expect(screen.getByRole("button", { name: "Approve plan" })).toBeInTheDocument();
  });

  it("hides approval from members", () => {
    renderAs("MEMBER");
    expect(screen.queryByRole("button", { name: "Approve plan" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Request changes" })).toBeInTheDocument();
  });

  it("asks for confirmation before cancelling and sends the revision", async () => {
    const fetchMock = vi.fn(async () => json(200, { ...workflow, status: "CANCELLED", revision: 3 }));
    vi.stubGlobal("fetch", fetchMock);
    document.cookie = "agentflow_csrf=token-1";
    renderAs("MEMBER");
    await userEvent.click(screen.getByRole("button", { name: "Cancel workflow" }));
    expect(fetchMock).not.toHaveBeenCalled();
    const confirm = await screen.findByRole("alertdialog");
    await userEvent.click(within(confirm).getByRole("button", { name: "Cancel workflow" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/workflows\/wf\/cancel$/);
    expect(JSON.parse(String(init.body))).toEqual({ revision: 2 });
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("token-1");
  });
});

describe("shared components", () => {
  it("labels statuses with text, not colour alone", () => {
    render(<StatusBadge status="AWAITING_APPROVAL" />);
    expect(screen.getByText("Awaiting approval")).toBeInTheDocument();
  });

  it("does not offer a retry for permission or not-found errors", () => {
    const error = (status: number) => new ApiError(status, { code: "X", message: "Nope.", request_id: "r-1", details: {} });
    const retry = vi.fn();
    const { rerender } = render(<ErrorState error={error(404)} onRetry={retry} />);
    expect(screen.getByText("Not found")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    rerender(<ErrorState error={error(503)} onRetry={retry} />);
    expect(screen.getByText("The server had a problem. Try again shortly.")).toBeInTheDocument();
    expect(screen.getByText("Reference: r-1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("agent configuration form", () => {
  it("keeps fields the form does not edit when saving a draft", () => {
    const base = {
      schema_version: 1 as const,
      system_instruction_ref: "builtin://writer/v1",
      model: { profile: "default", temperature: 0.2, max_output_tokens: 4096, credential_ref: "secret://openai" },
      capabilities: ["report_writing"],
      tools: [{ tool: "web_search", access: "read" as const }],
      input_schema_version: 2,
      output_schema_version: 3,
      limits: { timeout_seconds: 600, max_tokens_per_task: 5000 },
    };
    const values = agentFormSchema.parse({ ...agentFormDefaults({ name: "W", category: "writing", description: "", configuration: base }), timeoutSeconds: "120" });
    const config = toConfiguration(values, base);
    expect(config.model.credential_ref).toBe("secret://openai");
    expect(config.limits).toEqual({ timeout_seconds: 120, max_tokens_per_task: 5000 });
    expect(config.tools).toEqual([{ tool: "web_search", access: "read" }]);
    expect(config.input_schema_version).toBe(2);
  });

  it("rejects instruction text where a reference is expected", () => {
    const result = agentFormSchema.safeParse({ ...agentFormDefaults(), name: "A", capabilities: ["analysis"], instructionRef: "You are a helpful agent" });
    expect(result.success).toBe(false);
  });
});
