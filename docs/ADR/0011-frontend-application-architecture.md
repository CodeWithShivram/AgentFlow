# ADR-0011: Frontend application architecture

- Status: Accepted
- Date: 2026-11-09
- Resolves: OD-6 (frontend component system), as approved in the owner's Step 1.9 brief
- Builds on: ADR-0007 (authentication), ADR-0008 (agent registry), ADR-0009 (workflows), ADR-0010 (execution)

## Context
Step 1.9 builds the AgentFlow web application on the real API. The owner approved the stack:
- Next.js App Router, TypeScript strict, Tailwind CSS;
- shadcn/ui kept in the repository, on Radix UI, with lucide-react icons;
- React Hook Form + Zod for forms, TanStack Query for server state;
- Vitest + React Testing Library and Playwright.

The API already fixes the security model: tokens only in HttpOnly cookies, a readable CSRF cookie echoed in `X-CSRF-Token`, Origin checks, and RBAC that answers 404 to non-members.

## Decisions

### Rendering and routing
- **All API calls run in the browser.** The session cookies belong to the API host, so the Next.js server never sees them and never proxies them. Pages are server components that only render a client view inside `<Suspense>`.
- **Cache Components stay on** (`cacheComponents`, `partialPrefetching`). Dynamic segments (`/w/[workspaceId]/...`) render their client views inside Suspense, so the build prerenders a static shell for every route.
- **The workspace lives in the URL**, as `/w/<workspace id>/<section>`. There is no global client store. The shell resolves the workspace from the session's memberships, else from `GET /workspaces/{id}`, which returns 404 to non-members.

### Components
- shadcn/ui components are generated into `components/ui/` and owned by the repository. Its shared stylesheet is vendored as `styles/shadcn-tailwind.css` (MIT), so the `shadcn` CLI is not a runtime dependency; it brought production audit findings.
- `cn` (the shadcn-ui package) replaces `clsx` and `tailwind-merge`.
- System font stack, with no web-font download: builds and pages work offline.
- `hooks/use-mobile.ts` was adapted to `useSyncExternalStore`, to satisfy the React Compiler lint rule against setting state in effects.

### One API client (`lib/api/`)
- **`client.ts`** sends every request with `credentials: "include"` and `cache: "no-store"`.
  - Unsafe methods carry `X-CSRF-Token`, read from `agentflow_csrf` or `__Host-agentflow_csrf`.
  - Errors become `ApiError` (status, code, request ID, details, `Retry-After`) or `NetworkError`.
  - Non-JSON and 5xx bodies are never shown to the user.
- **Session refresh is serialized.**
  - On `401 AUTHENTICATION_REQUIRED`, every concurrent caller shares one `POST /auth/refresh` (single flight).
  - The refresh also takes a Web Locks API lock (`navigator.locks`), so two tabs never present the same refresh token at once; ADR-0007's reuse detection would otherwise revoke the session.
  - The original request is then replayed **once**. The API rejected it before any handler ran, so replaying cannot apply a change twice.
  - Without a CSRF cookie there is no session, so no refresh is attempted.
  - A failed refresh raises a "session expired" event.
  - Login and registration never refresh.
- **No silent retries of writes.**
  - Mutations have `retry: false`.
  - Reads retry only network errors and 5xx, at most twice, with backoff. 4xx responses are never retried.
- **Idempotency.** Workflow creation sends an `Idempotency-Key` generated once per form. A retried submission of the same form cannot create a second workflow.
- **`endpoints.ts`** has typed wrappers for exactly the backend routes. **`types.ts`** mirrors the Pydantic schemas: decimals stay strings, and the budget is `budget_limit` (OD-5).

### Authentication UX
- **`AuthProvider` restores the session** with `GET /auth/me`, refreshing through the client when needed. It exposes `loading`, `authenticated`, `anonymous` or `unavailable`.
- **Session expiry.**
  - All cached workspace data is dropped (`removeQueries`, except the session query).
  - `RequireAuth` sends the user to `/login?next=<path>&expired=1`.
  - `next` accepts only same-site absolute paths, so it cannot be used as an open redirect.
- **Logout** clears local state even if the API call fails.
- **Invitations.**
  - The one-time invitation code is shown once, in the inviter's dialog, and forgotten when the dialog closes.
  - The accept page takes it pasted, or from a URL fragment that is removed from history immediately.

### Server state, live updates and permissions
- **TanStack Query keys are scoped by workspace**, as `["workflows", workspaceId, ...]`.
- **Live updates use polling.** There is no SSE or WebSocket endpoint, so none is invented.
  - The workflow page polls the append-only event log incrementally (`?after=<last sequence>`) while the workflow is RUNNING, PAUSED or BLOCKED, or a task is in flight.
  - The interval starts at 2 s and backs off ×1.5 up to 15 s while nothing changes; it is 15 s after errors.
  - New events refresh the workflow, its tasks and their executions.
  - Polling pauses in hidden tabs.
- **Optimistic concurrency.** Every write sends the item's `revision`. A `*_REVISION_CONFLICT` opens a dialog that reloads the latest data; nothing is merged or retried automatically.
- **Permissions in the UI mirror the API** (`roleAtLeast`, the user-transition table in `features/workflows/status.ts`) only to hide actions that would fail. The API remains the authority, and an E2E test checks that a forged viewer request gets 403.

### Security headers
- `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy` and `Permissions-Policy` are set on every page, and `X-Powered-By` is removed.
- A Content-Security-Policy needs per-request nonces for Next.js inline scripts. It is deferred to the deployment work.

### Testing
- **Vitest + RTL**:
  - the API client (CSRF, refresh serialization, error mapping, no write retries);
  - navigation safety; form schemas; the status and permission tables;
  - event pagination; components.
- **Playwright** runs 11 journeys against the **real** stack:
  - API on :8011 and a production build on :3100;
  - a Celery worker and Beat;
  - a disposable `agentflow_e2e` database, reset on every run, and Redis databases 2 and 3.
  - The mock agent executes tasks, so no paid AI API is called.
  - An installed Chrome is used by default (`PLAYWRIGHT_CHANNEL`).

## Consequences
- **Deployment requirement.** The frontend must be able to read the CSRF cookie, so it must be served from the **same host** as the API: for example, one origin with a reverse proxy routing `/api` to the API. Cookies are host-only and port-insensitive, which is why `localhost:3000` with `localhost:8000` works in development. To be settled with OD-14 before any hosted deployment.
- **API gaps are shown in the UI, not faked** (see `IMPLEMENTATION_PLAN.md`, "Step 1.9 API gaps"):
  - member role changes and removal;
  - workspace rename; password change;
  - notifications; a workspace activity feed;
  - aggregate counts;
  - spend measurement.
- **The dashboard counts with one-item pages per status** (`?status=X&limit=1` → `total`): 13 small requests.
