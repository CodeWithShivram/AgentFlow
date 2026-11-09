# AgentFlow Progress

## Current position
- Phase: 1 — Foundation
- Last completed step: 1.9 — Frontend application
- Next step: 1.10 — Phase 1 integration and release (await explicit go-ahead). OD-13 (RLS) is owner-scheduled before 1.10.

## Completed steps
| Step | Commit message | Hash |
|---|---|---|
| 1.0 | `docs: establish AgentFlow implementation plan` | `16a9b59` |
| 1.1 | `chore: initialize AgentFlow monorepo` | `80e28ce` |
| 1.2 | `infra: add local PostgreSQL and Redis` | `9f8a550` |
| 1.3 | `feat(backend): establish API and infrastructure foundation` | `44eef4a` |
| 1.4 | `feat(database): add core AgentFlow schema and migrations` | `9543279` |
| 1.5 | `feat(auth): add authentication and workspace RBAC` | `7eded85` |
| 1.6 | `feat(agents): implement agent registry and versioning` | `baaedb9` |
| 1.7 | `feat(workflows): implement workflow and task management` | `82483e6` |
| 1.8 | `feat(execution): add reliable background execution with Celery` | `51e03f5` |
| 1.9 | `feat(frontend): add AgentFlow web application` | latest commit touching `frontend/lib/api/client.ts` |

## Environment snapshot (2026-11-09)
| Item | Value |
|---|---|
| Repo | `C:\Users\shivramm\project\AgentFlow`, branch `main`, nothing pushed |
| Python / uv / Node / npm | 3.12.14 via uv 0.12.9 / Node 24.13.0 / npm 11.6.2 |
| Celery / kombu | 5.7.0 / 5.7.0 |
| Docker | 29.8.2, Compose v5.6.0 — `agentflow` stack healthy |
| Dev database | `agentflow` at `0005 (head)`; built-ins seeded; `agentflow_worker` role provisioned |
| Local `.env` | now also holds a generated `WORKER_DB_PASSWORD` (gitignored) |
| Port 8000 | Used by another local project (`easa`); smoke tests used 8010 |
| E2E | API :8011, frontend :3100, database `agentflow_e2e` (reset each run), Redis DBs 2/3, installed Google Chrome |
| Frontend audit | `npm audit --omit=dev`: 0 vulnerabilities; dev-only: 8 moderate, all the existing `minimatch` chain via ESLint |

## Current implementation state (Step 1.9)
- **Architecture:** ADR-0011, which resolves OD-6.
  - Next.js 16 App Router with Cache Components; every API call runs in the browser.
  - Workspace in the URL: `/w/<id>/...`.
  - shadcn/ui in `components/ui/`, with its stylesheet vendored in `styles/`; system fonts.
- **`lib/api/`:** the single client.
  - Cookies; CSRF header on unsafe methods.
  - A single-flight refresh, also locked across tabs (Web Locks); a request is replayed once only after `AUTHENTICATION_REQUIRED`.
  - Typed `ApiError`/`NetworkError`; no write retries; `Idempotency-Key` on workflow creation.
- **`lib/auth/`:** `AuthProvider` restores the session (`/auth/me`), handles expiry (drops cached data, then `/login?expired=1`) and logout. `next` is open-redirect-safe.
- **Shell (`components/shell/`):**
  - collapsible sidebar, which becomes a sheet on mobile;
  - workspace switcher, breadcrumbs, activity entry point, theme menu, account menu;
  - skip link; loading, error and empty states.
- **Features (`features/`):**
  - dashboard (only real counts);
  - workflows: list with status filter and pagination; create with `budget_limit`/currency/policy; detail with lifecycle actions by role and policy, tasks CRUD, required/optional, dependencies dialog and stage graph, assignment, cancel/remove with confirmation, revision-conflict dialog, timeline, execution attempts, live polling with backoff;
  - agents: list, create, detail, edit draft, publish, activate/deactivate, versions;
  - proposals: submit; approve/reject with a note; self-review blocked in the UI and the API;
  - members and one-time invitation codes; accept invitation; workspace settings; account; activity.
- **Backend additions (read-only, Viewer/authenticated):**
  - `GET /api/v1/agent-catalog`;
  - `GET /api/v1/workspaces/{id}/workflows/{id}/tasks/{id}/executions`;
  - 3 integration tests.
- **Other changes:**
  - security headers in `next.config.ts`;
  - `scripts/check.ps1 -E2E` runs Playwright;
  - the E2E harness lives in `frontend/e2e/`.

## Checks last executed (Step 1.9)
- **`scripts/check.ps1 -E2E` ✔ (exit 0, "All checks passed"):**
  - backend ruff format/check, mypy, alembic check;
  - pytest **727 passed** (724 plus 3 new), 1 known `httpx2` warning (OD-10);
  - frontend eslint, typecheck;
  - Vitest **77 passed** (4 files);
  - Playwright **14 passed** (11 journeys plus 3 supporting tests);
  - `next build` (20 routes, partial prerendering).
- **Playwright ran against the real API, Celery worker and Beat, PostgreSQL and Redis.**
  - The journeys: register; login and session restore; navigation, desktop and mobile; agent lifecycle; proposal; workflow create; tasks and dependencies; assignment; execution monitoring (mock agent, attempt and version shown); expired session (silent refresh, then a dead session goes to sign in); viewer permissions with a forged 403; revision conflict.
  - It also checks: HttpOnly token cookies; no tokens in web storage; security headers; no horizontal overflow at 390 px on 8 pages.
  - No E2E processes were left running afterwards.
- **Accessibility review (manual plus tests):**
  - labelled controls with `aria-invalid`/`aria-describedby`;
  - errors announced (`role="alert"`); skip link; `aria-current` navigation;
  - Radix dialogs and menus trap focus and support Escape;
  - status shown as text, not only colour; the dependency graph is also described in text; keyboard-operable graph cards.
- **Security review:**
  - no `dangerouslySetInnerHTML`, `localStorage` or `sessionStorage` in app code;
  - API error text is shown only from the safe error envelope, and 5xx/non-JSON bodies are hidden;
  - the invitation code is shown once;
  - no secrets in `NEXT_PUBLIC_*`;
  - a secret scan of the diff found nothing.

## Deviations and findings
- **Deviations:**
  - Two read-only backend endpoints were added (listed above). Without them, agent forms would need hard-coded catalog data and attempts would be invisible.
  - The `use-mobile` hook (shadcn) was rewritten with `useSyncExternalStore` for the React Compiler lint rule.
  - The `shadcn` CLI package is not a dependency; its stylesheet is vendored. This avoids production audit findings.
- **Bug found by E2E and fixed:** the login form cleared the password after a failure with a delay, wiping a fast retype. It now keeps the value and focuses the field.
- **API gaps:** listed in `IMPLEMENTATION_PLAN.md` ("Step 1.9 API gaps"), each with the smallest proposed change. None is faked.

## Known issues / limitations
- **OD-13:** RLS is not enabled. The API and migrations still use the superuser; the worker role is already compatible.
- **OD-14:** trusted proxy IP handling, before hosted deployment.
- **OD-17:** a worker crash after claim leaves the task IN_PROGRESS; redelivery is deduplicated, so recovery is Phase 4.
- **OD-18:** FAILED outbox rows are kept but not surfaced or replayable yet (Phase 4).
- **OD-15:** database-level case-insensitive agent names. **OD-16:** replanning for tasks blocked by a cancelled dependency.
- Celery time limits are not enforced by the Windows `solo` pool, only by Linux prefork. Exactly one Beat must run.
- Dispatch latency is up to 5 s (the relay interval).
- Worker logs report `service: agentflow-api` (same SERVICE_NAME); a distinct worker service name can be configured.
- **OD-19:** the hosted frontend must share a host with the API (CSRF cookie readability); a CSP with nonces is not set yet.
- **Polling:** live updates poll, since there is no SSE endpoint. The dashboard makes 13 count requests, and Activity covers only the 10 newest workflows.
- **Still open:** OD-10 (the `httpx2` warning), the dev-only `minimatch` finding, and the Redis licence review.

## Blockers
None.

## Next exact action
On approval, start Step 1.10 (Phase 1 integration and release v0.1.0). Before it:
- OD-13 (enable RLS) is owner-scheduled;
- OD-10 and OD-15 are due.
