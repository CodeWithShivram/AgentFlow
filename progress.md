# AgentFlow Progress

## Current position
- Phase: 1 — Foundation
- Last completed step: 1.2 — Local infrastructure
- Next step: 1.3 — Backend foundation (await explicit go-ahead)

## Completed steps
| Step | Commit message | Hash |
|---|---|---|
| 1.0 | `docs: establish AgentFlow implementation plan` | `16a9b59` |
| 1.1 | `chore: initialize AgentFlow monorepo` | `80e28ce` |
| 1.2 | `infra: add local PostgreSQL and Redis` | latest commit touching `infrastructure/docker/docker-compose.yml` |

## Environment snapshot (2026-11-07)
| Item | Value |
|---|---|
| OS / shell | Windows 11 Pro, PowerShell 5.1 (Git Bash also available) |
| Repo root | `C:\Users\shivramm\project\AgentFlow`, branch `main`, remote `origin` = https://github.com/CodeWithShivram/AgentFlow.git (nothing pushed) |
| Git | 2.55.0; identity set repository-local only |
| Python | 3.12.14 via uv (baseline); system 3.14.7 via `py -3.14`; `python` not on PATH |
| uv | 0.12.9 (`C:\Users\shivramm\.local\bin` not on PATH; use `uv run`/`uv sync`) |
| Node / npm | v24.13.0 / 11.6.2 |
| Docker | 29.8.2 (Linux engine), Compose v5.6.0 — running |

## Current implementation state
- `backend/`: uv project (Python `>=3.12,<3.13`, `uv.lock`). FastAPI app factory `app.main:create_app`, route `GET /api/v1/health/live`. Ruff, mypy strict, pytest, httpx.
- `frontend/`: Next.js 16.4.5 App Router, React 19.3, strict TS, ESLint, Vitest 5 + RTL. Minimal shell page.
- `infrastructure/docker/docker-compose.yml` (project `agentflow`): `postgres:18.6-alpine` and `redis:8.10.3-alpine` (AOF on, password required), ports bound to `127.0.0.1`, named volumes `agentflow_postgres-data` / `agentflow_redis-data`, health checks. Credentials from repo-root `.env` (gitignored; local copy created with random passwords).
- `scripts/infra.ps1`: `up` (waits for healthy), `ps`, `logs`, `down`, `restart` (all keep volumes), `destroy-data` (requires typing DELETE).
- Root: `README.md`, `.env.example`, `.gitattributes`, `scripts/check.ps1`.

## Checks last executed (Step 1.2)
- `docker compose config --quiet` ✔; ports 5432/6379 free before start ✔
- `postgres:18.6-alpine` image declares `VOLUME /var/lib/postgresql`, `PGDATA=/var/lib/postgresql/18/docker` — volume mount matches ✔
- `infra.ps1 up`: both containers `healthy`, published as `127.0.0.1:5432` and `127.0.0.1:6379` ✔
- Host connectivity (throwaway script via `uv run --with psycopg[binary] --with redis`): PostgreSQL 18.6 query ✔, Redis 8.10.3 ping ✔, Redis without password rejected ✔
- Persistence: marker row/key written → `infra.ps1 restart` (containers removed and recreated) → both markers read back ✔ → markers deleted
- Logs: PostgreSQL "ready to accept connections", Redis "Ready to accept connections", no FATAL/ERROR ✔
- `scripts/check.ps1` (backend + frontend gates) ✔

## Known issues / limitations
- pytest warns `StarletteDeprecationWarning` (prefers `httpx2`). Kept `httpx` pending owner review.
- `npm audit` (dev deps only): moderate `minimatch` via `eslint-config-next@16.4.5`; not auto-fixed (would downgrade to Next 15 config).
- Redis 8 is distributed under RSALv2/SSPLv1/AGPLv3. Fine for local development; revisit (e.g. Valkey) before any hosted deployment.

## Blockers
None.

## Open decisions
OD-1…OD-6, OD-8 in `IMPLEMENTATION_PLAN.md`. OD-7 resolved (uv + `uv.lock`).

## Next exact action
On approval, start Step 1.3: Pydantic Settings (reading `.env`), structured logging with request IDs, centralized exception handling with the standard error shape, SQLAlchemy engine/session (psycopg 3 sync), `/api/v1/health/{ready,database,redis}` and tests.
