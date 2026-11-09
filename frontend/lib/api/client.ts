// The single HTTP client for the AgentFlow API (ADR-0007, ADR-0011).
//
// - Tokens live only in HttpOnly cookies set by the API; this code never sees them.
// - Unsafe requests echo the readable CSRF cookie in X-CSRF-Token.
// - A 401 AUTHENTICATION_REQUIRED triggers one refresh shared by every concurrent caller
//   (single flight); the original request is then replayed once. The API rejects such a
//   request before any handler runs, so replaying it cannot apply a change twice.
// - Nothing else is retried here; read retries are TanStack Query's decision.

import { ApiError, NetworkError, toApiError } from "./errors";

export type HttpMethod = "GET" | "POST" | "PATCH" | "DELETE";
export type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  body?: unknown;
  query?: Record<string, QueryValue>;
  headers?: Record<string, string>;
  /** Refresh the session and replay once on AUTHENTICATION_REQUIRED. Default true. */
  refreshOnAuthFailure?: boolean;
  signal?: AbortSignal;
}

export interface ApiClientOptions {
  baseUrl: string;
  fetchImpl?: typeof fetch;
  readCookies?: () => string;
}

export type SessionExpiredListener = () => void;

const CSRF_COOKIE_NAMES = ["__Host-agentflow_csrf", "agentflow_csrf"];
const CSRF_HEADER = "X-CSRF-Token";
const UNSAFE: ReadonlySet<HttpMethod> = new Set(["POST", "PATCH", "DELETE"]);

export function readCsrfToken(cookieString: string): string | null {
  for (const part of cookieString.split(";")) {
    const index = part.indexOf("=");
    if (index < 0) continue;
    const name = part.slice(0, index).trim();
    if (CSRF_COOKIE_NAMES.includes(name)) {
      const value = part.slice(index + 1).trim();
      if (value) return decodeURIComponent(value);
    }
  }
  return null;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly readCookies: () => string;
  private refreshInFlight: Promise<boolean> | null = null;
  private readonly expiredListeners = new Set<SessionExpiredListener>();

  constructor(options: ApiClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.fetchImpl = options.fetchImpl ?? ((input, init) => fetch(input, init));
    this.readCookies =
      options.readCookies ?? (() => (typeof document === "undefined" ? "" : document.cookie));
  }

  /** Called when a refresh fails: the session is gone and the user must sign in again. */
  onSessionExpired(listener: SessionExpiredListener): () => void {
    this.expiredListeners.add(listener);
    return () => {
      this.expiredListeners.delete(listener);
    };
  }

  get<T>(path: string, options?: RequestOptions): Promise<T> {
    return this.request<T>("GET", path, options);
  }

  post<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>("POST", path, { ...options, body });
  }

  patch<T>(path: string, body: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>("PATCH", path, { ...options, body });
  }

  delete<T>(path: string, options?: RequestOptions): Promise<T> {
    return this.request<T>("DELETE", path, options);
  }

  async request<T>(method: HttpMethod, path: string, options: RequestOptions = {}): Promise<T> {
    try {
      return await this.send<T>(method, path, options);
    } catch (error) {
      const canRefresh = options.refreshOnAuthFailure ?? true;
      if (!canRefresh || !isAuthenticationRequired(error)) throw error;
      if (!(await this.refresh())) {
        this.notifyExpired();
        throw error;
      }
      return this.send<T>(method, path, options);
    }
  }

  /** Rotates the session. Concurrent callers share one request. Resolves to success. */
  refresh(): Promise<boolean> {
    if (!this.refreshInFlight) {
      this.refreshInFlight = withCrossTabLock(() => this.doRefresh()).finally(() => {
        this.refreshInFlight = null;
      });
    }
    return this.refreshInFlight;
  }

  private async doRefresh(): Promise<boolean> {
    // Without the CSRF cookie there is no session to refresh (and the API would reject it).
    if (readCsrfToken(this.readCookies()) === null) return false;
    try {
      await this.send("POST", "/auth/refresh", {});
      return true;
    } catch (error) {
      if (error instanceof NetworkError) throw error;
      return false;
    }
  }

  private notifyExpired(): void {
    for (const listener of this.expiredListeners) listener();
  }

  private async send<T>(method: HttpMethod, path: string, options: RequestOptions): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (UNSAFE.has(method)) {
      const csrf = readCsrfToken(this.readCookies());
      if (csrf) headers[CSRF_HEADER] = csrf;
    }

    let response: Response;
    try {
      response = await this.fetchImpl(this.url(path, options.query), {
        method,
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        credentials: "include",
        cache: "no-store",
        signal: options.signal,
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw new NetworkError();
    }

    if (response.status === 204) return undefined as T;
    const payload = await readJson(response);
    if (!response.ok) {
      throw toApiError(response.status, payload, response.headers.get("Retry-After"));
    }
    return payload as T;
  }

  private url(path: string, query?: Record<string, QueryValue>): string {
    const url = `${this.baseUrl}/api/v1${path}`;
    if (!query) return url;
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
    }
    const search = params.toString();
    return search ? `${url}?${search}` : url;
  }
}

// Tabs share the refresh cookie. Two tabs presenting the same refresh token at once would look
// like token theft and revoke the session (ADR-0007), so refreshes are serialized across tabs.
async function withCrossTabLock<T>(fn: () => Promise<T>): Promise<T> {
  const locks = typeof navigator === "undefined" ? undefined : navigator.locks;
  return locks ? await locks.request("agentflow-session-refresh", fn) : fn();
}

function isAuthenticationRequired(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401 && error.code === "AUTHENTICATION_REQUIRED";
}

async function readJson(response: Response): Promise<unknown> {
  const type = response.headers.get("Content-Type") ?? "";
  if (!type.includes("application/json")) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}
