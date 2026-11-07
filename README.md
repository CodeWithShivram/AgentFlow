# AgentFlow

Universal multi-agent AI orchestration platform. A natural-language request is planned into a task graph, executed by specialised agents, independently verified, and reported with full history, usage and cost.

> Status: **Phase 1 — Foundation** in progress. Only a health endpoint and an application shell exist so far. See [`progress.md`](progress.md) and [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md). The full specification is [`AGENTFLOW_HANDOFF.txt`](AGENTFLOW_HANDOFF.txt).

## Repository layout

| Path | Contents |
|---|---|
| `backend/` | FastAPI API (and, from Step 1.8, Celery worker). Python 3.12, managed with uv. |
| `frontend/` | Next.js App Router UI. TypeScript strict, npm. |
| `infrastructure/docker/` | Docker Compose for local PostgreSQL and Redis. |
| `scripts/` | Developer scripts (`check.ps1` runs all quality gates, `infra.ps1` manages local infrastructure). |
| `docs/ADR/` | Architecture decision records. |

## Runtime versions

| Tool | Version |
|---|---|
| Python | 3.12 (3.12.14 tested) |
| uv | 0.12.9 |
| Node.js | 24.x (24.13.0 tested) |
| npm | 11.x (11.6.2 tested) |
| Next.js / React | 16.4.5 / 19.3.0 |
| FastAPI | 0.162.1 |
| PostgreSQL (Docker) | 18.6-alpine |
| Redis (Docker) | 8.10.3-alpine |
| Docker / Compose | 29.8.2 / v5.6.0 tested |

Exact dependency versions are locked in `backend/uv.lock` and `frontend/package-lock.json`.

## Prerequisites (Windows 11, PowerShell)

1. [uv](https://docs.astral.sh/uv/) — then `uv python install 3.12`.
2. Node.js 24 with npm.
3. Docker Desktop (required from Step 1.2 for PostgreSQL and Redis).

## Local infrastructure (PostgreSQL, Redis)

PostgreSQL 18.6 and Redis 8.10.3 run in Docker Compose (`infrastructure/docker/docker-compose.yml`), bound to `127.0.0.1` only, with named volumes and health checks.

One-time setup: copy `.env.example` to `.env` and replace each `change-me` password.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/infra.ps1 up        # start and wait until healthy
powershell -ExecutionPolicy Bypass -File scripts/infra.ps1 ps        # status
powershell -ExecutionPolicy Bypass -File scripts/infra.ps1 logs      # last 200 log lines
powershell -ExecutionPolicy Bypass -File scripts/infra.ps1 down      # stop (data kept)
powershell -ExecutionPolicy Bypass -File scripts/infra.ps1 restart   # recreate containers (data kept)
```

| Service | Host address | Credentials |
|---|---|---|
| PostgreSQL | `127.0.0.1:${POSTGRES_PORT}` (default 5432), database `${POSTGRES_DB}` | `POSTGRES_USER` / `POSTGRES_PASSWORD` from `.env` |
| Redis | `127.0.0.1:${REDIS_PORT}` (default 6379) | `REDIS_PASSWORD` from `.env` |

Data lives in the Docker volumes `agentflow_postgres-data` and `agentflow_redis-data`. `down` and `restart` never delete them. To wipe all local data deliberately, run `scripts/infra.ps1 destroy-data`, which asks you to type `DELETE` first. This cannot be undone.

## Backend

```powershell
cd backend
uv sync                                   # creates backend\.venv with Python 3.12 + dev tools
uv run uvicorn app.main:app --reload --port 8000
```

- Liveness: http://localhost:8000/api/v1/health/live
- OpenAPI docs: http://localhost:8000/docs

Checks:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy app tests
uv run pytest
```

## Frontend

```powershell
cd frontend
npm ci
npm run dev                               # http://localhost:3000
```

Checks:

```powershell
npm run lint
npm run typecheck
npm test
npm run build
```

## All checks

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check.ps1
```

## Configuration

Copy `.env.example` to `.env` for local values. Never commit `.env` or secrets.
