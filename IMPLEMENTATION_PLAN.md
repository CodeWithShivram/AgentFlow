# AgentFlow Implementation Plan

Statuses: `NOT_STARTED` · `IN_PROGRESS` · `BLOCKED` · `DONE`
Every step ends with exactly one verified commit (see `CLAUDE.md`). Complete and verify each phase before starting the next.

Phase 1 steps come directly from the handoff (§17). Phases 2–8 are numbered here from the handoff's phase bullets; refine their acceptance criteria at the start of each phase and record changes in this file.

---

## Phase 1 — Foundation

| Step | Title | Status | Commit |
|---|---|---|---|
| 1.0 | Repository reconnaissance and implementation plan | DONE | `16a9b59` |
| 1.1 | Monorepo and developer tooling | DONE | `80e28ce` |
| 1.2 | Local infrastructure (PostgreSQL, Redis) | DONE | `9f8a550` |
| 1.3 | Backend foundation | DONE | `44eef4a` |
| 1.4 | Database and migrations | DONE | `9543279` |
| 1.5 | Authentication and workspace authorization | DONE | `7eded85` |
| 1.6 | Agent registry CRUD | DONE | `baaedb9` |
| 1.7 | Workflow and task management | DONE | `82483e6` |
| 1.8 | Background execution foundation | DONE | `51e03f5` |
| 1.9 | Frontend application | DONE | `b3cce86` |
| 1.9a | Database hardening: row-level security (OD-13, OD-15, OD-10) | DONE | `889d989` |
| 1.10 | Phase 1 integration and release v0.1.0 | DONE (CI not yet run on GitHub) | see `progress.md`; tag `v0.1.0` |

### 1.0 — Repository reconnaissance and implementation plan
- Depends on: —
- Acceptance: repo root confirmed; Git initialized with remote; tools inventoried; `CLAUDE.md`, `progress.md`, `IMPLEMENTATION_PLAN.md`, initial ADRs created; blockers and open decisions recorded; no application code.
- Commit: `docs: establish AgentFlow implementation plan`

### 1.1 — Monorepo and developer tooling
- Depends on: 1.0; OD-7 resolved (uv.lock).
- Acceptance: `backend/` with `pyproject.toml` (Python 3.12, FastAPI, Ruff, pytest, pinned/locked deps) and a health-only API; `frontend/` Next.js App Router + strict TS + Vitest with a minimal shell; `.env.example`; README with exact runtime versions; lint/format/test commands work; both apps start.
- Commit: `chore: initialize AgentFlow monorepo`

### 1.2 — Local infrastructure
- Depends on: 1.1; Docker Desktop running.
- Acceptance: `infrastructure/docker/docker-compose.yml` with PostgreSQL and Redis bound to `127.0.0.1`, named volumes, health checks; documented start/stop/logs/reset; host connection verified; routine reset does not delete volumes.
- Commit: `infra: add local PostgreSQL and Redis`

### 1.3 — Backend foundation
- Depends on: 1.2.
- Acceptance (as revised by the owner's Step 1.3 brief): Pydantic Settings from the root `.env` with validation and dev/test/prod behaviour; JSON logging with request IDs and secret redaction; request ID middleware; centralized exception handling with the standard error shape; SQLAlchemy 2 **async** engine/session with psycopg 3 (ADR-0005); async Redis client; lifespan-managed cleanup; `/api/v1/health/{live,ready,db,redis}` (owner chose `db` over the handoff's `database`); unit and real-infrastructure integration tests.
- Commit: `feat(backend): establish API and infrastructure foundation`

### 1.4 — Database and migrations
- Depends on: 1.3; OD-4 resolved (ADR-0006).
- Acceptance: models and an Alembic migration for users, workspaces, workspace_members, agents, agent_versions, workflows, workflow_tasks, task_dependencies, workflow_events and idempotency_keys; UUID PKs, UTC timestamps, naming convention, FKs, indexes, unique and check constraints; workspace isolation through composite FKs; append-only events trigger; idempotency service (409 on payload mismatch, exactly-once under concurrency, atomic with the business change, no secrets cached, documented expiry and cleanup); fresh upgrade, downgrade, rollback/reapply and stairway tests on disposable databases; no model/migration drift.
- Commit: `feat(database): add core AgentFlow schema and migrations`

### 1.5 — Authentication and workspace authorization
- Depends on: 1.4; OD-1, OD-2, OD-3 resolved (ADR-0007).
- Acceptance (as revised by the owner's Step 1.5 brief): register/login/refresh/logout/me; Argon2id; JWT access tokens (kid key ring, pinned algorithm, iss/aud/exp/nbf validated, no roles); hashed opaque refresh tokens with rotation, family revocation on reuse; cookie-only transport (HttpOnly, SameSite, host-only, Secure in production, restricted refresh path); session-bound CSRF tokens; Origin validation; strict CORS; cookie-based WebSocket auth; personal workspace + OWNER membership created atomically at signup; invitations; centralized RBAC returning 404 to non-members; Redis rate limiting failing closed; RLS designed (enabling tracked as OD-13); tests for auth, rotation, reuse, expiry, logout, signup rollback, RBAC, isolation, concurrency, cookies and log hygiene.
- Commit: `feat(auth): add authentication and workspace RBAC`

### 1.6 — Agent registry CRUD
- Depends on: 1.5.
- Acceptance (as revised by the owner's Step 1.6 brief): agent definitions with draft, revision and immutable published versions; strict configuration schema (instruction and model references, catalog capabilities and tools, secret references only); six built-ins seeded from configuration; list/create/get/update-draft/publish/activate/deactivate/versions APIs; dynamic proposals with separate storage, frozen content, authorized approval (not by the author), approval not activating; OWNER required for HIGH-risk tools; tasks pin exact agent versions; append-only audit; tests for CRUD, isolation, RBAC, immutability, version pinning, validation, proposals, tool permissions, concurrency and audit (ADR-0008).
- Commit: `feat(agents): implement agent registry and versioning`

### 1.7 — Workflow and task management
- Depends on: 1.6; OD-5 and OD-11 resolved (ADR-0009).
- Acceptance (as revised by the owner's Step 1.7 brief): workflow create/list/detail with idempotent creation; task CRUD; dependencies with self/duplicate/cross-workflow/cycle rejection; central workflow and task transition services implementing the approved matrices with actor rules; row locks plus revisions for concurrency; events written atomically; `budget_limit`/currency validation; assignment pinning the active published agent version with skill checks; completion only when required tasks are COMPLETED; workflow kept consistent with task state; tests for every transition, DAG rules, isolation, concurrency, idempotency, events, budget and migrations.
- Commit: `feat(workflows): implement workflow and task management`

### 1.8 — Background execution foundation
- Depends on: 1.7; OD-8, OD-9, OD-12 resolved (ADR-0010).
- Acceptance (as revised by the owner's Step 1.8 brief): Celery + Redis (JSON only, late acks, prefetch 1, execution/maintenance queues, Windows solo pool, worker and Beat entry points); transactional outbox written with the state change, relay with SKIP LOCKED claims, leases, lease-guarded acknowledgement, retained failures and at-least-once semantics; idempotent consumers via inbox; sync worker sessions as a restricted non-superuser role; mock execution recording `agent_executions`; hourly bounded idempotent cleanup preserving security records; real PostgreSQL/Redis tests including a real-worker end-to-end test.
- Commit: `feat(execution): add reliable background execution with Celery`

### 1.9 — Frontend application
- Depends on: 1.5–1.8 APIs; OD-6 resolved (ADR-0011).
- Acceptance, as revised by the owner's Step 1.9 brief:
  - **Built on the real API only:** no mock production APIs, fake authentication or invented metrics.
  - **Shell:** responsive app shell with mobile navigation.
  - **Auth:** cookie authentication with CSRF; serialized refresh; expiry handling.
  - **Features:**
    - a dashboard;
    - agents, versions and proposals;
    - workflows with filters and pagination, tasks, dependencies, assignment, lifecycle actions, revision conflicts, budget and currency;
    - execution monitoring by polling;
    - members and invitations.
  - **Robustness and accessibility:** the 401/403/404/409/422/429/5xx errors handled; no silent retries of writes; accessible and responsive.
  - **Tests:** Vitest unit/component/integration tests, and 11 Playwright journeys against the real backend.
  - **Backend:** the smallest additions where a gap blocked a screen. These are `GET /agent-catalog` and `GET .../tasks/{id}/executions`; the other gaps are documented below.
- Commit: `feat(frontend): add AgentFlow web application`

#### Step 1.9 API gaps
The UI states each of these where it applies; none is faked.

| Gap | Today | Smallest compatible backend change |
|---|---|---|
| Agent options (capabilities, categories, tools) | **Added in 1.9:** `GET /api/v1/agent-catalog` | — |
| Task execution attempts | **Added in 1.9:** `GET .../workflows/{id}/tasks/{task_id}/executions` | — |
| Change a member's role, remove a member | Members page explains it is unavailable | `PATCH`/`DELETE /workspaces/{id}/members/{user_id}` (ADMIN+, OWNER for admins), reusing the invite role rules |
| Rename a workspace | Settings are read-only | `PATCH /workspaces/{id}` with `name` (ADMIN+) |
| Change password or email | Account page is read-only | `POST /auth/password` (current + new password; revokes other sessions) |
| Notifications | The bell opens Activity | A notifications table + `GET /notifications` (Phase 6/8) |
| Workspace-wide activity feed | Activity merges the events of the 10 newest workflows (first 500 events each) | `GET /workspaces/{id}/events?after=` across workflows |
| Aggregate counts | Dashboard uses `?status=X&limit=1` totals (13 requests) | `GET /workspaces/{id}/workflows/summary` returning counts by status |
| Recently updated ordering | Lists are newest-created first | `sort=updated_at` on the list endpoints |
| Spend and token usage | "Not measured yet (Phase 2)" | Model gateway usage records (Phase 2) |
| Live push | Polling the event log with backoff | SSE stream of workflow events (Phase 4) |

### 1.9a — Database hardening: row-level security
- Inserted by the owner before 1.10 (separate step and commit). Resolves OD-13, OD-15, OD-10 (ADR-0012).
- Acceptance:
  - **Roles:** the API connects as a restricted `agentflow_api` role (no superuser, no BYPASSRLS, owns nothing).
  - **Policies:** `ENABLE`/`FORCE` RLS policies on every workspace-owned table, through group roles. Workers keep a platform policy.
  - **Context:** a per-transaction `app.user_id` context set by authentication and registration.
  - **Joining:** a user can join a workspace only as its owner or with an invitation. Built-in agents are read-only.
  - **Tests:** isolation proven with unfiltered SQL; no context left over on pooled connections; every API integration test runs as the restricted role.
  - **OD-15:** a `(workspace_id, lower(name))` unique index, with the concurrent-race test.
  - **OD-10:** tests use `httpx2`.
- Commit: `feat(security): enable PostgreSQL row-level security`

### 1.10 — Phase 1 integration and release
- Depends on: 1.1–1.9.
- Acceptance: full backend and frontend suites pass; migrations from clean state; frontend, API, worker, PostgreSQL, Redis running together; registration/login, workspace isolation, agent CRUD, workflow/task CRUD and mock worker verified; GitHub Actions CI (backend-ci, frontend-ci) green; README, progress and release notes updated.
- Owner decisions (2026-11-12): RLS delivered first as Step 1.9a; no push, so "CI green" is verified by running both workflows' commands in Linux containers (Python 3.12 / Node 24) and becomes confirmed on the first push; local annotated tag `v0.1.0`.
- Delivered: `.github/workflows/backend-ci.yml` and `frontend-ci.yml`; `CHANGELOG.md` (release notes); full stack (API, worker, Beat, PostgreSQL, Redis, frontend) verified from a clean database by the Playwright journeys.
- Commit: `release: complete AgentFlow foundation v0.1.0`

---

## Phase 2 — Intelligent planning

| Step | Title | Status |
|---|---|---|
| 2.1 | Model gateway abstraction and mock model adapter | DONE (ADR-0013) — `d2aef8f` |
| 2.2 | Structured prompt analysis (requirements/constraints) | DONE (ADR-0014) — `8dffe47` |
| 2.3 | Planner output schema, task decomposition, acceptance criteria | DONE (ADR-0015) — `f1d8476` |
| 2.4 | DAG validation and plan preview API | DONE (ADR-0016) — `8389c82` |
| 2.5 | User plan confirmation before execution | DONE (ADR-0017) — see `progress.md` |

Acceptance (phase): deterministic tests with mock model responses; invalid plans/cycles rejected; plan visible before execution.

## Phase 3 — Specialized agents

| Step | Title | Status |
|---|---|---|
| 3.1 | BaseAgent contract and AgentSubmission schema | NOT_STARTED |
| 3.2 | Tool registry and server-side permission enforcement | NOT_STARTED |
| 3.3 | LiteLLM integration behind gateway adapter + provider usage capture | NOT_STARTED |
| 3.4 | Research, Analyst, Writer, General agents | NOT_STARTED |
| 3.5 | Developer and QA agents (no unsafe shell/code execution) | NOT_STARTED |
| 3.6 | Rule-based agent selection with persisted rationale | NOT_STARTED |
| 3.7 | Agent proposal workflow with admin approval before activation | NOT_STARTED |

## Phase 4 — Orchestration and scheduling

| Step | Title | Status |
|---|---|---|
| 4.1 | LangGraph workflow state and nodes | NOT_STARTED |
| 4.2 | PostgreSQL checkpoint persistence | NOT_STARTED |
| 4.3 | Celery dispatch and worker lifecycle (outbox hardening, idempotent claims) | NOT_STARTED |
| 4.4 | Dependency-aware scheduling and concurrency limits | NOT_STARTED |
| 4.5 | Cancellation and restart recovery | NOT_STARTED |
| 4.6 | Parallel and sequential execution tests | NOT_STARTED |

## Phase 5 — Verification and recovery

| Step | Title | Status |
|---|---|---|
| 5.1 | Deterministic verification layer | NOT_STARTED |
| 5.2 | Semantic evaluation with structured evidence | NOT_STARTED |
| 5.3 | Rework instructions, bounded retries and reassignment | NOT_STARTED |
| 5.4 | Replan with DAG validation | NOT_STARTED |
| 5.5 | Escalation policies; only platform verification accepts tasks | NOT_STARTED |

## Phase 6 — Human intervention

| Step | Title | Status |
|---|---|---|
| 6.1 | Clarification and approval request model/API | NOT_STARTED |
| 6.2 | Workflow pause/resume | NOT_STARTED |
| 6.3 | Expiration and rejection behaviour | NOT_STARTED |
| 6.4 | Approval audit records and per-action authorization | NOT_STARTED |

## Phase 7 — Observability and cost

| Step | Title | Status |
|---|---|---|
| 7.1 | Centralized usage tracker and pricing snapshots | NOT_STARTED |
| 7.2 | Cost calculation and reconciliation (actual vs estimated) | NOT_STARTED |
| 7.3 | Per-workflow/agent/model breakdown and analytics API | NOT_STARTED |
| 7.4 | Event timeline and execution report | NOT_STARTED |
| 7.5 | Latency, retry and failure metrics | NOT_STARTED |

## Phase 8 — Product completion

| Step | Title | Status |
|---|---|---|
| 8.1 | Workflow DAG visualization | NOT_STARTED |
| 8.2 | Analytics and final report UI | NOT_STARTED |
| 8.3 | Artifact management (storage interface, checksums, scoping) | NOT_STARTED |
| 8.4 | Safe workflow replay | NOT_STARTED |
| 8.5 | Security review and load tests | NOT_STARTED |
| 8.6 | Backup/restore and operational runbooks | NOT_STARTED |
| 8.7 | Production deployment design (design only) | NOT_STARTED |

---

## Open decisions (must be resolved before the listed step)

| ID | Decision | Needed by | Notes |
|---|---|---|---|
| OD-1 | ~~Access token format~~ **Resolved 2026-11-07: JWT access + hashed rotating refresh tokens (ADR-0007)** | 1.5 | Approved by owner. |
| OD-2 | ~~Browser auth transport~~ **Resolved 2026-11-07: HttpOnly cookies + CSRF + Origin checks (ADR-0007)** | 1.5 | Approved by owner. |
| OD-3 | ~~Workspace on registration~~ **Resolved 2026-11-07: personal workspace + OWNER, atomically (ADR-0007)** | 1.5 | Approved by owner. |
| OD-4 | ~~Idempotency key storage~~ **Resolved 2026-11-07: `idempotency_keys` table (ADR-0006)** | 1.4 | Approved by owner. |
| OD-5 | ~~Budget field naming~~ **Resolved 2026-11-09: `budget_limit` everywhere, no alias (ADR-0009)** | 1.7 | Approved by owner. |
| OD-6 | ~~Frontend component system~~ **Resolved 2026-11-09: shadcn/ui (in-repo) on Radix, Tailwind, React Hook Form + Zod, TanStack Query (ADR-0011)** | 1.9 | Approved by owner in the Step 1.9 brief. |
| OD-7 | ~~Python dependency locking~~ **Resolved 2026-11-07: uv project + `uv.lock`** | 1.1 | Approved by owner. |
| OD-8 | ~~Outbox scope~~ **Resolved 2026-11-09: minimal reliable outbox now, advanced recovery in Phase 4 (ADR-0010)** | 1.8 | Approved by owner. |
| OD-9 | ~~Worker DB access~~ **Resolved 2026-11-09: sync SQLAlchemy + psycopg 3 as restricted `agentflow_worker` (ADR-0010)** | 1.8 | Approved by owner. |
| OD-10 | ~~Replace `httpx` with `httpx2`~~ **Resolved 2026-11-12 in Step 1.9a (ADR-0012)** | before 1.10 | Tests use `httpx2`; the deprecation warning is gone. |
| OD-11 | ~~Workflow statuses~~ **Resolved 2026-11-09: 10 statuses and approved transition matrices (ADR-0009)** | 1.7 | Approved by owner. |
| OD-12 | ~~Cleanup scheduling~~ **Resolved 2026-11-09: hourly Celery Beat cleanup (ADR-0010)** | 1.8 | Approved by owner. |
| OD-13 | ~~Enable PostgreSQL row-level security~~ **Resolved 2026-11-12 in Step 1.9a (ADR-0012)** | before 1.10 (owner-scheduled) | Restricted `agentflow_api` role, per-transaction `app.user_id`, policies via group roles, tests. Non-superuser schema owner moved to OD-20. |
| OD-14 | Trusted reverse-proxy client IP handling for rate limits | before any proxied deployment | Rate limits use the direct peer IP today. |
| OD-15 | ~~Database-level case-insensitive agent names~~ **Resolved 2026-11-12 in Step 1.9a (ADR-0012)** | before 1.10 | Unique index `uq_agents_workspace_id_lower_name`. |
| OD-16 | Replanning / recovery for tasks blocked by a cancelled dependency | Phase 5 | Today only cancelling the workflow resolves it (ADR-0009). |
| OD-17 | Recover executions stranded by a worker crash after claim (task stays IN_PROGRESS) | Phase 4 | Needs heartbeat/time-out sweep (ADR-0010). |
| OD-18 | Surface and replay FAILED outbox events; dead-letter tooling | Phase 4 | Rows are kept, not yet visible (ADR-0010). |
| OD-19 | Hosted frontend topology and Content-Security-Policy | before any hosted deployment | The app must read the API's CSRF cookie, so the frontend and API must share a host (e.g. a reverse proxy routing `/api`). A CSP needs per-request nonces for Next.js inline scripts (ADR-0011). |
| OD-20 | Non-superuser schema owner for migrations | before any hosted deployment | Migrations still run as the administrator (`POSTGRES_USER`); applications never connect as it (ADR-0012). |
