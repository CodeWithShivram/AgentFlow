# AgentFlow Progress

## Current position
- Phase: 1 — Foundation
- Last completed step: 1.1 — Monorepo and developer tooling
- Next step: 1.2 — Local infrastructure (await explicit go-ahead; needs Docker Desktop running)

## Completed steps
| Step | Commit message | Hash |
|---|---|---|
| 1.0 | `docs: establish AgentFlow implementation plan` | `16a9b59` |
| 1.1 | `chore: initialize AgentFlow monorepo` | latest commit touching `backend/pyproject.toml` (`git log -1 -- backend/pyproject.toml`) |

## Environment snapshot (2026-11-07)
| Item | Value |
|---|---|
| OS / shell | Windows 11 Pro, PowerShell 5.1 (Git Bash also available) |
| Repo root | `C:\Users\shivramm\project\AgentFlow`, branch `main`, remote `origin` = https://github.com/CodeWithShivram/AgentFlow.git (nothing pushed) |
| Git | 2.55.0; identity set repository-local only |
| Python | 3.12.14 via uv (baseline); system 3.14.7 via `py -3.14`; `python` not on PATH |
| uv | 0.12.9 (`C:\Users\shivramm\.local\bin` not on PATH; use `uv run`/`uv sync`) |
| Node / npm | v24.13.0 / 11.6.2 |
| Docker | 29.8.2, Compose v5.6.0 — daemon not running at last check |

## Current implementation state
- `backend/`: uv project (Python `>=3.12,<3.13`, `uv.lock`). FastAPI app factory `app.main:create_app`, route `GET /api/v1/health/live`. Dev tools: Ruff, mypy (strict + pydantic plugin), pytest, httpx.
- `frontend/`: Next.js 16.4.5 App Router, React 19.3, strict TS, ESLint (next core-web-vitals + typescript), Vitest 5 + React Testing Library (jsdom). Minimal shell page with an explicit "not available yet" notice. Exact versions (`.npmrc save-exact`), `package-lock.json`.
- Root: `README.md` (setup and versions), `.env.example` (no variables required yet), `.gitattributes` (LF, CRLF for `.ps1`), `scripts/check.ps1` (all gates).

## Checks last executed (Step 1.1)
- `scripts/check.ps1`: ruff format --check ✔, ruff check ✔, mypy (9 files) ✔, pytest 2 passed ✔, eslint ✔, `next typegen && tsc --noEmit` ✔, vitest 2 passed ✔, `next build` ✔
- Smoke: `uvicorn app.main:app` → `/api/v1/health/live` = `{"status":"ok"}`, `/openapi.json` 200
- Smoke: `next dev` → `/` 200 with `<h1>AgentFlow</h1>`
- `npm audit --omit=dev`: 0 vulnerabilities

## Known issues / limitations
- pytest warns `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2`. Kept `httpx` (handoff baseline) pending owner review of switching to `httpx2`.
- `npm audit` (dev deps): moderate `minimatch` ReDoS via `typescript-eslint` ← `eslint-config-next@16.4.5`. Dev tooling only; auto-fix would downgrade to Next 15 config, so not applied. Re-check on next eslint-config-next update.
- Frontend scaffold `@types/node` was `^20`; changed to `24.19.2` to match Node 24 and satisfy Vitest 5 peer requirements.

## Blockers
- B-1: Docker Desktop not running. Required for Step 1.2.

## Open decisions
OD-1…OD-6, OD-8 in `IMPLEMENTATION_PLAN.md`. OD-7 resolved (uv + `uv.lock`).

## Next exact action
On approval and with Docker Desktop running, start Step 1.2: `infrastructure/docker/docker-compose.yml` with PostgreSQL and Redis on 127.0.0.1, named volumes, health checks; document start/stop/logs/reset; verify host connectivity.
