import { describe, expect, it, vi } from "vitest";

import { ApiClient, readCsrfToken } from "./client";
import { createEndpoints } from "./endpoints";
import { ApiError, describeError, NetworkError } from "./errors";

const BASE = "http://api.test";

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function apiError(status: number, code: string, message = "msg", details: Record<string, unknown> = {}) {
  return json(status, { error: { code, message, request_id: "req-1", details } });
}

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>;

function setup(handler: Handler, cookies = "agentflow_csrf=csrf-1") {
  const calls: { url: string; init: RequestInit }[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init: init ?? {} });
    return handler(url, init ?? {});
  });
  let cookieString = cookies;
  const client = new ApiClient({ baseUrl: BASE, fetchImpl, readCookies: () => cookieString });
  return {
    client,
    api: createEndpoints(client),
    calls,
    fetchImpl,
    setCookies: (value: string) => {
      cookieString = value;
    },
  };
}

const header = (init: RequestInit, name: string) => (init.headers as Record<string, string>)[name];

describe("readCsrfToken", () => {
  it("reads the development and the __Host- production cookie names", () => {
    expect(readCsrfToken("a=1; agentflow_csrf=abc; b=2")).toBe("abc");
    expect(readCsrfToken("__Host-agentflow_csrf=xyz")).toBe("xyz");
    expect(readCsrfToken("agentflow_access=secret")).toBeNull();
    expect(readCsrfToken("")).toBeNull();
  });
});

describe("ApiClient requests", () => {
  it("sends cookies and the CSRF header on unsafe requests only", async () => {
    const { api, calls } = setup(() => json(200, { items: [], total: 0, limit: 50, offset: 0 }));
    await api.workflows.list("ws-1", { status: "RUNNING", limit: 5 });
    await api.workflows.start("ws-1", "wf-1", 3);

    expect(calls[0].url).toBe(`${BASE}/api/v1/workspaces/ws-1/workflows?status=RUNNING&limit=5`);
    expect(calls[0].init.credentials).toBe("include");
    expect(header(calls[0].init, "X-CSRF-Token")).toBeUndefined();

    expect(calls[1].url).toBe(`${BASE}/api/v1/workspaces/ws-1/workflows/wf-1/start`);
    expect(calls[1].init.method).toBe("POST");
    expect(calls[1].init.credentials).toBe("include");
    expect(header(calls[1].init, "X-CSRF-Token")).toBe("csrf-1");
    expect(JSON.parse(String(calls[1].init.body))).toEqual({ revision: 3 });
  });

  it("encodes path segments and drops empty query values", async () => {
    const { api, calls } = setup(() => json(200, { items: [], total: 0, limit: 50, offset: 0 }));
    await api.agents.list("ws/../x", { status: undefined, offset: 0 });
    expect(calls[0].url).toBe(`${BASE}/api/v1/workspaces/ws%2F..%2Fx/agents?offset=0`);
  });

  it("sends the Idempotency-Key when creating a workflow", async () => {
    const { api, calls } = setup(() => json(201, { id: "wf-1" }));
    await api.workflows.create(
      "ws-1",
      {
        prompt: "p",
        settings: {
          autonomy_policy: "approval_required",
          budget_limit: "10.5",
          currency: "EUR",
          max_parallel_tasks: 3,
          allow_dynamic_agent_proposals: false,
          deadline: null,
        },
      },
      "key-123",
    );
    expect(header(calls[0].init, "Idempotency-Key")).toBe("key-123");
  });

  it("returns undefined for 204 responses", async () => {
    const { api } = setup(() => new Response(null, { status: 204 }));
    await expect(api.tasks.remove("ws", "wf", "t", 2)).resolves.toBeUndefined();
  });
});

describe("error handling", () => {
  it.each([
    [403, "INSUFFICIENT_ROLE"],
    [404, "WORKFLOW_NOT_FOUND"],
    [409, "TASK_REVISION_CONFLICT"],
    [422, "VALIDATION_ERROR"],
  ])("maps %i responses to ApiError with the API code", async (status, code) => {
    const { api } = setup(() => apiError(status, code));
    const error = await api.workflows.get("ws", "wf").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(status);
    expect((error as ApiError).code).toBe(code);
    expect((error as ApiError).requestId).toBe("req-1");
  });

  it("recognises revision conflicts", async () => {
    const { api } = setup(() => apiError(409, "WORKFLOW_REVISION_CONFLICT"));
    const error = (await api.workflows.start("ws", "wf", 1).catch((e: unknown) => e)) as ApiError;
    expect(error.isRevisionConflict).toBe(true);
  });

  it("explains 422 field errors and 429 retry-after", async () => {
    const { api } = setup((url) =>
      url.includes("tasks")
        ? apiError(422, "VALIDATION_ERROR", "Request validation failed.", {
            errors: [{ loc: ["body", "title"], message: "String should have at least 1 character", type: "x" }],
          })
        : apiError(429, "RATE_LIMITED", "Too many requests.", {}),
    );
    const validation = await api.tasks.list("ws", "wf").catch((e: unknown) => e);
    expect(describeError(validation)).toBe("title: String should have at least 1 character");

    const { api: limited } = setup(() =>
      new Response(JSON.stringify({ error: { code: "RATE_LIMITED", message: "x", request_id: null, details: {} } }), {
        status: 429,
        headers: { "Content-Type": "application/json", "Retry-After": "17" },
      }),
    );
    const rateLimited = (await limited.auth.me().catch((e: unknown) => e)) as ApiError;
    expect(rateLimited.retryAfterSeconds).toBe(17);
    expect(describeError(rateLimited)).toBe("Too many requests. Try again in 17 seconds.");
  });

  it("never shows server error text and tolerates non-JSON bodies", async () => {
    const { api } = setup(() => new Response("<html>Traceback: secret</html>", { status: 502 }));
    const error = await api.workflows.get("ws", "wf").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(describeError(error)).toBe("The server had a problem. Try again shortly.");
    expect(describeError(error)).not.toContain("secret");
  });

  it("reports network failures without retrying writes", async () => {
    const { api, fetchImpl } = setup(() => {
      throw new TypeError("Failed to fetch");
    });
    const error = await api.workflows.start("ws", "wf", 1).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(NetworkError);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});

describe("session refresh", () => {
  it("refreshes once and replays the request after AUTHENTICATION_REQUIRED", async () => {
    let authenticated = false;
    const { api, calls } = setup((url) => {
      if (url.endsWith("/auth/refresh")) {
        authenticated = true;
        return json(200, {});
      }
      return authenticated ? json(200, { user: { id: "u" }, memberships: [] }) : apiError(401, "AUTHENTICATION_REQUIRED");
    });
    await expect(api.auth.me()).resolves.toMatchObject({ user: { id: "u" } });
    expect(calls.map((c) => c.url.replace(BASE, ""))).toEqual([
      "/api/v1/auth/me",
      "/api/v1/auth/refresh",
      "/api/v1/auth/me",
    ]);
    expect(header(calls[1].init, "X-CSRF-Token")).toBe("csrf-1");
  });

  it("serializes refresh: concurrent 401s share one refresh request", async () => {
    let authenticated = false;
    let release: () => void = () => undefined;
    const refreshGate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { api, calls } = setup(async (url) => {
      if (url.endsWith("/auth/refresh")) {
        await refreshGate;
        authenticated = true;
        return json(200, {});
      }
      return authenticated ? json(200, { items: [], total: 0, limit: 1, offset: 0 }) : apiError(401, "AUTHENTICATION_REQUIRED");
    });
    const pending = Promise.all([
      api.workflows.list("ws"),
      api.agents.list("ws"),
      api.proposals.list("ws"),
      api.workspaces.members("ws"),
    ]);
    await vi.waitFor(() => expect(calls.filter((c) => c.url.endsWith("/auth/refresh"))).toHaveLength(1));
    release();
    await expect(pending).resolves.toHaveLength(4);
    expect(calls.filter((c) => c.url.endsWith("/auth/refresh"))).toHaveLength(1);
  });

  it("reports an expired session when the refresh fails, without replaying", async () => {
    const { client, api, calls } = setup((url) =>
      url.endsWith("/auth/refresh") ? apiError(401, "INVALID_REFRESH_TOKEN") : apiError(401, "AUTHENTICATION_REQUIRED"),
    );
    const expired = vi.fn();
    client.onSessionExpired(expired);
    const error = (await api.workflows.start("ws", "wf", 1).catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("AUTHENTICATION_REQUIRED");
    expect(expired).toHaveBeenCalledTimes(1);
    expect(calls.filter((c) => c.url.endsWith("/start"))).toHaveLength(1);
  });

  it("does not try to refresh without a CSRF cookie (no session)", async () => {
    const { client, api, calls } = setup(() => apiError(401, "AUTHENTICATION_REQUIRED"), "");
    const expired = vi.fn();
    client.onSessionExpired(expired);
    await expect(api.auth.me()).rejects.toBeInstanceOf(ApiError);
    expect(calls).toHaveLength(1);
    expect(expired).toHaveBeenCalledTimes(1);
  });

  it("never refreshes for wrong credentials on login", async () => {
    const { api, calls } = setup(() => apiError(401, "INVALID_CREDENTIALS"));
    await expect(api.auth.login({ email: "a@b.co", password: "x" })).rejects.toMatchObject({
      code: "INVALID_CREDENTIALS",
    });
    expect(calls).toHaveLength(1);
  });

  it("does not refresh on CSRF or permission failures", async () => {
    const { api, calls } = setup(() => apiError(403, "CSRF_TOKEN_INVALID"));
    await expect(api.workflows.start("ws", "wf", 1)).rejects.toMatchObject({ status: 403 });
    expect(calls).toHaveLength(1);
  });
});
