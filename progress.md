# AgentFlow Progress

## Current position
- Phase: 1 — Foundation
- Last completed step: 1.4 — Database and migrations
- Next step: 1.5 — Authentication and workspace authorization (await explicit go-ahead; needs OD-1, OD-2, OD-3)

## Completed steps
| Step | Commit message | Hash |
|---|---|---|
| 1.0 | `docs: establish AgentFlow implementation plan` | `16a9b59` |
| 1.1 | `chore: initialize AgentFlow monorepo` | `80e28ce` |
| 1.2 | `infra: add local PostgreSQL and Redis` | `9f8a550` |
| 1.3 | `feat(backend): establish API and infrastructure foundation` | `44eef4a` |
| 1.4 | `feat(database): add core AgentFlow schema and migrations` | latest commit touching `backend/alembic/versions/0001_core_schema.py` |

## Environment snapshot (2026-11-07)
| Item | Value |
|---|---|
| OS / shell | Windows 11 Pro, PowerShell 5.1 (Git Bash also available) |
| Repo root | `C:\Users\shivramm\project\AgentFlow`, branch `main`, remote `origin` = https://github.com/CodeWithShivram/AgentFlow.git (nothing pushed) |
| Git | 2.55.0; identity set repository-local only |
| Python | 3.12.14 via uv (baseline); system 3.14.7 via `py -3.14`; `python` not on PATH |
| uv | 0.12.9 (`C:\Users\shivramm\.local\bin` not on PATH; use `uv run`/`uv sync`) |
| Node / npm | v24.13.0 / 11.6.2 |
| Docker | 29.8.2 (Linux engine), Compose v5.6.0 — running; `agentflow` stack up |
| Dev database | `agentflow` migrated to revision `0001 (head)` |

## Current implementation state
- Backend foundation from Step 1.3 (config, JSON logging, request IDs, errors, async DB/Redis, health endpoints).
- `backend/app/models/`: `base.py` (naming convention, UUID/timestamp helpers, `str_enum` VARCHAR+CHECK), `tenancy.py` (User, Workspace, WorkspaceMember, WorkspaceRole), `agent.py` (Agent, AgentVersion, AgentStatus), `workflow.py` (Workflow, WorkflowTask, TaskDependency, WorkflowEvent and status/event enums), `idempotency.py` (IdempotencyKey, IdempotencyStatus).
- `backend/alembic/`: sync psycopg env (URL from Settings or test override), revision `0001_core_schema` (hand-reviewed; circular agents↔agent_versions FK added after both tables; append-only trigger on `workflow_events`).
- `backend/app/repositories/idempotency.py` and `backend/app/services/idempotency.py`: `IdempotencyService.begin/complete/fail/purge_expired`; `request_fingerprint`; errors mapped to 409/400.
- `backend/app/core/errors.py`: `AppError` base + handler (standard error shape). `backend/app/core/sensitive_data.py`: credential-like key detection.
- New setting `IDEMPOTENCY_KEY_TTL_SECONDS` (default 86400).
- `scripts/check.ps1` now also runs `mypy … alembic` and `alembic check` (requires the dev DB at head).

## Checks last executed (Step 1.4)
- `alembic upgrade head`, `downgrade base`, `upgrade head` on the dev DB ✔; `alembic check`: "No new upgrade operations detected" ✔
- `scripts/check.ps1` ✔: ruff format/check, mypy strict (44 files), alembic check, pytest **143 passed** (83 unit, 60 integration), eslint, typecheck, vitest 2 passed, next build
- Migration tests on disposable DBs: fresh upgrade creates all 10 tables, all model constraint/index names and the trigger; schema matches models (no autogenerate diff); downgrade to base leaves only `alembic_version` and removes the trigger function; rollback+reapply yields an identical schema; stairway per revision; downgrade with data present
- Mutation checks (each reverted): skipping the payload-hash check → 1 failure; removing `ON CONFLICT` → 6 failures; not creating the append-only trigger → 1 failure; task FK without `workspace_id` → 1 failure
- No `agentflow_test_*` databases left behind
- API smoke test against migrated dev DB: `/api/v1/health/ready` 200 (database ok, redis ok)

## Known issues / limitations
- pytest warns `StarletteDeprecationWarning` (prefers `httpx2`). Tracked as OD-10.
- `npm audit` (dev deps only): moderate `minimatch` via `eslint-config-next@16.4.5`; not auto-fixed.
- Redis 8 licence (RSALv2/SSPLv1/AGPLv3): fine locally; revisit before hosted deployment.
- `WorkflowStatus` values are provisional until Step 1.7 (OD-11).
- Expired idempotency keys are purged lazily (on key reuse) or by calling `purge_expired()`; periodic scheduling arrives with Celery in Step 1.8 (OD-12).
- A concurrent duplicate idempotent request waits for the first request's transaction to finish (by design, ADR-0006).
- `updated_at` is maintained by the ORM (`onupdate`), not by a database trigger; raw SQL updates must set it.
- Multi-task DAG cycle detection is application-level (Step 1.7); the database only rejects self-dependencies and cross-workflow edges.
- Row-level security is not enabled; isolation relies on composite FKs plus workspace-scoped queries (ADR-0006).

## Blockers
None.

## Open decisions
OD-1, OD-2, OD-3 (needed for Step 1.5), OD-5, OD-6, OD-8, OD-9, OD-10, OD-11, OD-12 in `IMPLEMENTATION_PLAN.md`. OD-4 and OD-7 resolved.

## Next exact action
On approval and with OD-1 (token format), OD-2 (browser auth transport for SSE) and OD-3 (workspace on registration) decided, start Step 1.5: registration/login with Argon2id, access tokens, roles, and workspace authorization enforced in services/repositories with cross-workspace denial tests.
