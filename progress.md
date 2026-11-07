# AgentFlow Progress

## Current position
- Phase: 1 — Foundation
- Last completed step: 1.3 — Backend foundation
- Next step: 1.4 — Database and migrations (await explicit go-ahead; needs OD-4 decided)

## Completed steps
| Step | Commit message | Hash |
|---|---|---|
| 1.0 | `docs: establish AgentFlow implementation plan` | `16a9b59` |
| 1.1 | `chore: initialize AgentFlow monorepo` | `80e28ce` |
| 1.2 | `infra: add local PostgreSQL and Redis` | `9f8a550` |
| 1.3 | `feat(backend): establish API and infrastructure foundation` | latest commit touching `backend/app/core/config.py` |

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

## Current implementation state
- `backend/app/core/`: `config.py` (Pydantic Settings from repo-root `.env`, validation, production placeholder-secret rejection, docs disabled in production), `logging.py` (JSON lines, request ID, key/URL/secret-value redaction, uvicorn loggers routed through it), `request_id.py` (validated `X-Request-ID`, contextvar, pure ASGI middleware with request-completed log), `errors.py` (standard error shape, handlers for HTTP/validation/unhandled errors), `redis.py` (async client, lazy connect, client name).
- `backend/app/db/session.py`: async engine (psycopg 3, pooled, pre-ping, connect timeout), session factory, `get_db_session` dependency (rollback on error, always closed, no auto-commit), Windows Proactor-loop guard.
- `backend/app/services/health.py`: DB/Redis checks with timeouts, readiness runs both concurrently; failures logged by error type only.
- `backend/app/api/v1/health.py`: `/health/live`, `/health/ready`, `/health/db`, `/health/redis`.
- `backend/app/main.py`: `create_app(settings)` factory; lifespan creates engine/Redis, closes Redis then disposes engine (engine disposed even if Redis close fails).
- Run: `uv run uvicorn app.main:create_app --factory --reload --port 8000 --loop asyncio:SelectorEventLoop`.
- Frontend and infrastructure unchanged since Step 1.2.

## Checks last executed (Step 1.3)
- `uv run ruff format --check .`, `uv run ruff check .` ✔; `uv run mypy app tests` (27 files, strict) ✔
- `uv run pytest`: 67 passed (60 unit, 7 integration against local PostgreSQL 18.6 / Redis 8.10.3)
- Mutation check: injecting a validation-input leak and disabling secret-value redaction made 4 tests fail; restored → all pass
- Integration pre-check verified: with Redis port pointed at a closed port, tests fail with the "run scripts/infra.ps1 up" message
- Live server (`--loop asyncio:SelectorEventLoop`): `/live` 200 ok, `/ready` 200 ready (database ok, redis ok), `/db` 200 ok, `/redis` 200 ok; `X-Request-ID` echoed; logs are JSON; PostgreSQL password absent from server logs
- Live server with `--reload`: `/ready` 200
- Live server without `--loop` on Windows: startup refused with "Async psycopg cannot run on the Windows ProactorEventLoop…", exit code 3
- `scripts/check.ps1` (all backend + frontend gates) ✔

## Known issues / limitations
- pytest warns `StarletteDeprecationWarning` (prefers `httpx2`). Tracked as OD-10.
- `npm audit` (dev deps only): moderate `minimatch` via `eslint-config-next@16.4.5`; not auto-fixed.
- Redis 8 licence (RSALv2/SSPLv1/AGPLv3): fine locally; revisit before hosted deployment.
- Unhandled exceptions are logged twice: once by the app handler (JSON, with request ID) and once by uvicorn when Starlette re-raises. Both are redacted JSON; this is deliberate (not swallowed).
- Graceful shutdown cleanup is verified through the ASGI lifespan in tests (connections disappear from `pg_stat_activity` and Redis `CLIENT LIST`), not by signalling a live uvicorn process.

## Blockers
None.

## Open decisions
OD-1…OD-6, OD-8, OD-9, OD-10 in `IMPLEMENTATION_PLAN.md`. OD-4 (idempotency key storage) is needed for Step 1.4.

## Next exact action
On approval and with OD-4 decided, start Step 1.4: SQLAlchemy models for users, workspaces, workspace_members, agents, agent_versions, workflows, workflow_tasks, task_dependencies, workflow_events; Alembic migrations; upgrade/downgrade verified on a disposable test database.
