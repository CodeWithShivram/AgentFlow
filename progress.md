# AgentFlow Progress

## Current position
- Phase: 1 — Foundation
- Last completed step: 1.5 — Authentication and workspace authorization
- Next step: 1.6 — Agent registry CRUD (await explicit go-ahead)

## Completed steps
| Step | Commit message | Hash |
|---|---|---|
| 1.0 | `docs: establish AgentFlow implementation plan` | `16a9b59` |
| 1.1 | `chore: initialize AgentFlow monorepo` | `80e28ce` |
| 1.2 | `infra: add local PostgreSQL and Redis` | `9f8a550` |
| 1.3 | `feat(backend): establish API and infrastructure foundation` | `44eef4a` |
| 1.4 | `feat(database): add core AgentFlow schema and migrations` | `9543279` |
| 1.5 | `feat(auth): add authentication and workspace RBAC` | latest commit touching `backend/app/services/auth.py` |

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
| Dev database | `agentflow` migrated to revision `0002 (head)` |
| Local `.env` | now also holds a generated `AUTH_JWT_SIGNING_KEYS` / `AUTH_JWT_ACTIVE_KEY_ID` (gitignored) |

## Current implementation state
- Steps 1.3–1.4 foundation: config, JSON logging, request IDs, errors, async DB/Redis, health, schema 0001, idempotency.
- Migration `0002`: `auth_sessions` (refresh-token family, CSRF hash, absolute expiry, revocation reason), `refresh_tokens` (SHA-256 hash, single use), `workspace_invitations` (token hash, role ≠ OWNER).
- `app/core/`: `passwords.py` (Argon2id in a thread; dummy verify for unknown emails), `tokens.py` (HS256 JWT with key ring and `kid`, pinned algorithm, required claims; opaque tokens and hashing), `cookies.py`, `origin.py` (Origin/Referer check for unsafe methods and WebSockets), `rate_limit.py` (Redis fixed window, fails closed), `errors.py` (`AppError` carries headers).
- `app/repositories/`: `users.py`, `auth_sessions.py` (row-locked token lookup), `workspaces.py`.
- `app/services/`: `auth.py` (register/login/refresh/logout; rotation and reuse revocation), `authorization.py` (role hierarchy, `authorize_workspace`, `can_grant`), `workspaces.py` (invitations).
- `app/api/deps.py` (`CurrentPrincipal`, `workspace_role()`, `get_websocket_principal`, rate limiter), `app/api/v1/auth.py`, `app/api/v1/workspaces.py`; CORS and Origin middleware in `app/main.py`.
- New settings: `AUTH_JWT_*`, `AUTH_*_TTL_SECONDS`, `AUTH_SESSION_MAX_AGE_SECONDS`, `AUTH_COOKIE_SECURE`, `AUTH_LOGIN_ATTEMPTS_PER_MINUTE`, `AUTH_REQUESTS_PER_MINUTE`, `WORKSPACE_INVITATION_TTL_SECONDS`, `CORS_ALLOWED_ORIGINS`.

## Checks last executed (Step 1.5)
- `alembic upgrade` 0001→0002, `downgrade -1`, re-upgrade on the dev DB ✔; `alembic check` clean ✔
- `scripts/check.ps1` ✔: ruff format/check, mypy strict (66 files), alembic check, pytest **248 passed** (141 unit, 107 integration), eslint, typecheck, vitest 2, next build
- Migration tests (fresh upgrade, no drift, downgrade, rollback/reapply, stairway) now cover both revisions
- Mutation checks (each reverted), all caught: no reuse detection (1 failure), no CSRF check (2), non-members treated as owners (1), revoked sessions still accepted (2), Origin check disabled (7), rate limiter failing open (1)
- Live uvicorn smoke run of the auth flow: **not completed** (see Known issues)

## Known issues / limitations
- **Live smoke test not completed.** The first attempt reached a stale server: an orphaned `--reload` worker (PID 20008) left running by my Step 1.3 smoke test was still bound to 127.0.0.1:8000 and serving Step 1.3 code. It was found and stopped. The retry was blocked by a permission check because its cleanup filter was too broad. **Correction to earlier reports:** Step 1.3's "port 8000 listeners: 0" check was wrong (the `-State Listen` filter missed the socket), and Step 1.4's live `/ready` check may have reached that stale process. Integration tests through the full ASGI app remain the evidence for both steps.
- Row-level security is designed but not enabled (OD-13); the app DB role is a superuser that bypasses RLS.
- Rate limits use the direct peer IP; behind a proxy this needs trusted forwarding (OD-14).
- Registration reveals whether an email exists (409); there is no email verification yet. Invitation tokens are returned to the inviter rather than emailed.
- Concurrent refreshes with the same token revoke the session (strict reuse detection); the frontend must serialize refreshes (Step 1.9).
- Expired sessions, refresh tokens and invitations are not yet purged (OD-12).
- No non-browser (API key / bearer) authentication.
- Still open from earlier: `httpx2` deprecation warning (OD-10), dev-only `minimatch` audit finding, Redis 8 licence review, provisional workflow statuses (OD-11).

## Blockers
None for Step 1.6.

## Open decisions
OD-5, OD-6, OD-8, OD-9, OD-10, OD-11, OD-12, OD-13, OD-14 in `IMPLEMENTATION_PLAN.md`. OD-1–OD-4 and OD-7 resolved.

## Next exact action
Optionally re-run the live auth smoke test (start uvicorn, run register → me → refresh → logout with `Origin: http://localhost:3000`, then stop the server by its own process ID). On approval, start Step 1.6: agent definitions with immutable versions, capability and tool-permission schemas, CRUD/enable/disable APIs behind `workspace_role`, and seeding of the six built-in agents.
