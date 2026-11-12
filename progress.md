# AgentFlow Progress

## Current position
- Phase: 1 — Foundation
- Last completed step: 1.9a — Database hardening: row-level security (OD-13, OD-15, OD-10)
- Next step: 1.10 — Phase 1 integration and release v0.1.0 (approved by the owner: no push, CI unverified until pushed, local tag `v0.1.0`).

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
| 1.9 | `feat(frontend): add AgentFlow web application` | `b3cce86` |
| 1.9a | `feat(security): enable PostgreSQL row-level security` | latest commit touching `backend/alembic/versions/0006_row_level_security.py` |

## Environment snapshot (2026-11-12)
| Item | Value |
|---|---|
| Repo | `C:\Users\shivramm\project\AgentFlow`, branch `main`, nothing pushed |
| Python / uv / Node / npm | 3.12.14 via uv 0.12.9 / Node 24.13.0 / npm 11.6.2 |
| Celery / kombu | 5.7.0 / 5.7.0 |
| Docker | 29.8.2, Compose v5.6.0 — `agentflow` stack healthy (Docker Desktop must be started first) |
| Dev database | `agentflow` at `0006 (head)`; built-ins seeded; `agentflow_api` and `agentflow_worker` provisioned |
| Local `.env` | holds generated `WORKER_DB_PASSWORD` and (new) `API_DB_PASSWORD` (gitignored) |
| Port 8000 | Used by another local project (`easa`); smoke tests used 8010 |
| E2E | API :8011 (as `agentflow_api`), frontend :3100, database `agentflow_e2e` (reset each run), Redis DBs 2/3, installed Google Chrome |
| Frontend audit | `npm audit --omit=dev`: 0 vulnerabilities; dev-only: 8 moderate, all the existing `minimatch` chain via ESLint |
| Test HTTP client | `httpx2` 2.16.0 (replaces `httpx`, OD-10) |

## Current implementation state (Step 1.9a)
- **ADR-0012:** resolves OD-13, OD-15 and OD-10, and opens OD-20.
- **Roles (`app/db/roles.py`):**
  - The API connects as `agentflow_api` (`API_DB_USER`/`API_DB_PASSWORD`): NOSUPERUSER, NOBYPASSRLS, NOINHERIT, owns nothing.
  - It has SELECT/INSERT/UPDATE/DELETE on application tables, but not on `alembic_version` or `inbox_messages`.
  - `POSTGRES_USER` is the administrator only: migrations, `app.agents.seed`, provisioning.
  - `uv run python -m app.db.roles provision` provisions both roles; re-run it after migrations.
- **Group roles:** `agentflow_rls_tenant` (the API) and `agentflow_rls_platform` (workers) are NOLOGIN with no privileges. Membership is granted `WITH INHERIT TRUE`, and policies target only these groups.
- **Migration `0006`:**
  - `ENABLE` and `FORCE` RLS on 13 workspace-owned tables;
  - functions `app_current_user_id()`, `app_user_workspace_ids()` (SECURITY DEFINER) and `app_can_join_workspace()`;
  - tenant policies plus read-only built-in agents and versions;
  - the workspace owner visible during registration;
  - joining a workspace only as its owner or with a valid invitation;
  - a platform policy for workers;
  - OD-15: the `uq_agents_workspace_id_lower_name` index.
- **Per-transaction context (`app/db/session.py`):**
  - `TenantSession` with an `after_begin` hook, plus `set_session_user()`, which runs `set_config('app.user_id', …, true)`.
  - It is set by authentication, by registration after creating the user, and by the session responses before reading memberships.
- **Service fix:** `_lock_owned` checks for built-ins before `FOR UPDATE`. RLS blocks locking rows outside the UPDATE policy, so built-ins keep their 403 `AGENT_READ_ONLY` instead of turning into 404.

## Checks last executed (Step 1.9a)
- **`scripts/check.ps1 -E2E` ✔ (exit 0, "All checks passed"):**
  - ruff format/check; mypy (109 files); alembic check clean;
  - pytest **739 passed**, with **no warnings** (the `httpx` deprecation is gone);
  - frontend eslint, typecheck; Vitest **77 passed**;
  - Playwright **14 passed**, with the E2E API running as the restricted role under RLS;
  - `next build`;
  - no E2E processes were left running.
- **Every API integration test runs as a provisioned restricted role** (`agentflow_api_it`), so the whole API suite runs under RLS.
- **New tests:**
  - **`test_row_level_security.py`** (10 tests):
    - RLS is enabled and forced on exactly the expected tables;
    - the API role's attributes, ownership, groups and excluded tables;
    - cross-tenant reads, inserts, updates, deletes and moves with **no application filter**;
    - no rows without a context, while built-ins stay readable;
    - built-ins are read-only;
    - no leftover context on the same pooled connection after commit or rollback;
    - joining a workspace only by invitation;
    - policies target only the group roles;
    - the case-insensitive name index.
  - **`test_agents_service.py`:** four concurrent case-variant creates leave exactly one agent; the others get `AGENT_NAME_TAKEN`.
  - **`test_config.py`:** the API URL, the required password, and the production placeholder rejection.
- **Migration stairway, downgrade to base and rollback-reapply** pass with `0006`. The dev DB was upgraded to `0006`.
- **Deliberate-bug checks** (each reverted afterwards), both caught:
  - roles provisioned with `BYPASSRLS`: 7 of 10 RLS tests fail;
  - the authentication context removed: 48 API test errors.

## Deviations and findings
- **Schema owner.** It stays the administrator (`POSTGRES_USER`), not ADR-0007's planned non-superuser owner; tracked as OD-20. Applications never connect as it.
- **Worker policy.** It is unscoped: the relay and cleanup are cross-tenant. The worker is still limited by its table grants (ADR-0010).
- **Context storage.** The user is kept on `session.info`, not in a contextvar.
- **Tables outside RLS,** as ADR-0007 designed: `users`, `auth_sessions`, `refresh_tokens`, `workspace_invitations`, `inbox_messages`.
- **Pre-existing flaky test fixed.** `test_tokens.py` built its claims at collection time. Once the suite passed 5 minutes, the `nbf` case became valid, and every other case was being rejected for expiry rather than its own reason. Claims are now built when each test runs, and each case first checks that a baseline token is valid.

## Known issues / limitations
- **OD-20:** a non-superuser schema owner for migrations, before any hosted deployment.
- **OD-14:** trusted proxy IP handling, before hosted deployment.
- **OD-19:** the hosted frontend must share a host with the API (CSRF cookie readability); a CSP with nonces is not set yet.
- **OD-17:** a worker crash after claim leaves the task IN_PROGRESS; recovery is Phase 4.
- **OD-18:** FAILED outbox rows are kept but not surfaced or replayable yet (Phase 4).
- **OD-16:** replanning for tasks blocked by a cancelled dependency.
- **Celery:** time limits are not enforced by the Windows `solo` pool, only by Linux prefork. Exactly one Beat must run. Dispatch latency is up to 5 s.
- **Polling:** live updates poll, since there is no SSE endpoint. The dashboard makes 13 count requests, and Activity covers only the 10 newest workflows.
- **Still open:** the dev-only `minimatch` finding and the Redis licence review.

## Blockers
None.

## Next exact action
Start Step 1.10:
- GitHub Actions `backend-ci` and `frontend-ci`, written and validated locally (no push, so CI stays unverified);
- a clean-state migration plus the full stack together;
- release notes; README, plan and progress;
- the commit `release: complete AgentFlow foundation v0.1.0`;
- a local annotated tag `v0.1.0`.
