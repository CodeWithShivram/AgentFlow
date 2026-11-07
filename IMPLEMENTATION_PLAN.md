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
| 1.2 | Local infrastructure (PostgreSQL, Redis) | DONE | see `progress.md` |
| 1.3 | Backend foundation | NOT_STARTED | |
| 1.4 | Database and migrations | NOT_STARTED | |
| 1.5 | Authentication and workspace authorization | NOT_STARTED | |
| 1.6 | Agent registry CRUD | NOT_STARTED | |
| 1.7 | Workflow and task management | NOT_STARTED | |
| 1.8 | Background execution foundation | NOT_STARTED | |
| 1.9 | Frontend foundation | NOT_STARTED | |
| 1.10 | Phase 1 integration and release v0.1.0 | NOT_STARTED | |

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
- Acceptance: Pydantic Settings; structured logging with request IDs; centralized exception handling with the standard error shape; SQLAlchemy engine/session (psycopg 3 sync); `/api/v1/health/{live,ready,database,redis}`; tests for config and health.
- Commit: `feat(backend): establish API and infrastructure foundation`

### 1.4 — Database and migrations
- Depends on: 1.3; decision OD-4 (idempotency key storage).
- Acceptance: models for users, workspaces, workspace_members, agents, agent_versions, workflows, workflow_tasks, task_dependencies, workflow_events (+ idempotency storage if decided); UUID PKs, UTC timestamps, FKs, indexes, unique constraints; Alembic upgrade and downgrade verified on a disposable test DB.
- Commit: `feat(database): add core AgentFlow schema and migrations`

### 1.5 — Authentication and workspace authorization
- Depends on: 1.4; decisions OD-1 (token format), OD-2 (SSE/browser auth transport), OD-3 (workspace on registration).
- Acceptance: register/login; Argon2id; short-lived access tokens; refresh rotation with hashed storage if used; roles Owner/Admin/Member/Viewer; ownership enforced in services/repositories; cross-workspace access denial tested.
- Commit: `feat(auth): add authentication and workspace RBAC`

### 1.6 — Agent registry CRUD
- Depends on: 1.5.
- Acceptance: agent definitions with immutable versions; capability and tool-permission schemas; list/detail/create/update/enable/disable APIs; six initial agents seeded from configuration; authorization and validation tests.
- Commit: `feat(agents): implement agent registry and versioning`

### 1.7 — Workflow and task management
- Depends on: 1.6; decision OD-5 (`max_budget` vs `budget_limit` naming).
- Acceptance: workflow create/list/detail; task CRUD and dependencies; DAG cycle and self-dependency rejection; central state transition service; persisted workflow events; idempotent workflow creation; transition and dependency tests.
- Commit: `feat(workflows): implement workflow and task management`

### 1.8 — Background execution foundation
- Depends on: 1.7; ADR-0004 (Celery on Windows); decision OD-8 (outbox scope).
- Acceptance: Celery + Redis configured; mock worker runs a harmless task; agent_executions records (migration added here); outbox-based dispatch; bounded retry and timeout; duplicate delivery produces no duplicate accepted side effects (tested).
- Commit: `feat(execution): add mock background task execution`

### 1.9 — Frontend foundation
- Depends on: 1.5–1.8 APIs; decision OD-6 (component system).
- Acceptance: auth screens; dashboard shell and navigation; workflow list/create/detail skeletons; agent registry screen; typed API client; mock data clearly labelled; loading/error/empty states; component tests.
- Commit: `feat(frontend): add AgentFlow application shell`

### 1.10 — Phase 1 integration and release
- Depends on: 1.1–1.9.
- Acceptance: full backend and frontend suites pass; migrations from clean state; frontend, API, worker, PostgreSQL, Redis running together; registration/login, workspace isolation, agent CRUD, workflow/task CRUD and mock worker verified; GitHub Actions CI (backend-ci, frontend-ci) green; README, progress and release notes updated.
- Commit: `release: complete AgentFlow foundation v0.1.0`

---

## Phase 2 — Intelligent planning

| Step | Title | Status |
|---|---|---|
| 2.1 | Model gateway abstraction and mock model adapter | NOT_STARTED |
| 2.2 | Structured prompt analysis (requirements/constraints) | NOT_STARTED |
| 2.3 | Planner output schema, task decomposition, acceptance criteria | NOT_STARTED |
| 2.4 | DAG validation and plan preview API | NOT_STARTED |
| 2.5 | User plan confirmation before execution | NOT_STARTED |

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
| OD-1 | Access token format (e.g. JWT vs opaque DB-backed) and whether refresh tokens are used | 1.5 | Handoff requires short-lived access tokens; refresh optional. |
| OD-2 | Browser auth transport (httpOnly cookie vs bearer header) | 1.5 | Browser `EventSource` cannot set headers; SSE (§11) must be authenticated. |
| OD-3 | Does registration auto-create a workspace with the user as Owner? | 1.5 | Not specified. |
| OD-4 | Idempotency key storage (table/columns, TTL) | 1.4 | Required by §11 but absent from §10 schema. |
| OD-5 | API `settings.max_budget` vs DB `budget_limit` naming | 1.7 | Example request and schema differ. |
| OD-6 | Frontend component system | 1.9 | "Accessible, consistent", avoid unnecessary libraries. |
| OD-7 | ~~Python dependency locking~~ **Resolved 2026-11-07: uv project + `uv.lock`** | 1.1 | Approved by owner. |
| OD-8 | Outbox scope in 1.8 vs Phase 4 | 1.8 | Proposed: minimal outbox in 1.8, hardening in 4.3. |
