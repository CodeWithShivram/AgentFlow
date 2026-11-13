# AgentFlow

Universal multi-agent AI orchestration platform. A natural-language request is planned into a task graph, executed by specialised agents, independently verified, and reported with full history, usage and cost.

> Status: **v0.1.0 — Phase 1 (Foundation) complete.** The API (auth, workspaces with row-level security, agent registry, workflows, background execution with a mock agent) and the web application are in place. Agents do not call a model yet (Phase 2). See [`CHANGELOG.md`](CHANGELOG.md), [`progress.md`](progress.md) and [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md). The full specification is [`AGENTFLOW_HANDOFF.txt`](AGENTFLOW_HANDOFF.txt).

## Repository layout

| Path | Contents |
|---|---|
| `backend/` | FastAPI API and Celery worker. Python 3.12, managed with uv. |
| `frontend/` | Next.js App Router UI. TypeScript strict, npm. |
| `infrastructure/docker/` | Docker Compose for local PostgreSQL and Redis. |
| `scripts/` | Developer scripts (`check.ps1` runs all quality gates, `infra.ps1` manages local infrastructure). |
| `docs/ADR/` | Architecture decision records. |
| `.github/workflows/` | GitHub Actions: `backend-ci`, `frontend-ci`. |

## Runtime versions

| Tool | Version |
|---|---|
| Python | 3.12 (3.12.14 tested) |
| uv | 0.12.9 |
| Node.js | 24.x (24.13.0 tested) |
| npm | 11.x (11.6.2 tested) |
| Next.js / React | 16.4.5 / 19.3.0 |
| FastAPI | 0.162.1 |
| SQLAlchemy / psycopg / redis-py | 2.1.8 / 3.3.8 / 8.1.0 |
| Alembic | 1.20.1 |
| Celery / kombu | 5.7.0 / 5.7.0 |
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

Requires the repo-root `.env` (see Configuration). Start the infrastructure first.

```powershell
cd backend
uv sync                                   # creates backend\.venv with Python 3.12 + dev tools
uv run alembic upgrade head               # create or update the database schema
uv run python -m app.agents.seed          # load/refresh the six built-in agents (idempotent)
uv run python -m app.db.roles provision    # create/refresh the restricted API and worker DB roles
uv run uvicorn app.main:create_app --factory --reload --port 8000 --loop asyncio:SelectorEventLoop
```

The API connects as the restricted `agentflow_api` role (set `API_DB_PASSWORD` in `.env`); PostgreSQL row-level security limits it to the signed-in user's workspaces. `POSTGRES_USER` is only the administrator for migrations, seeding and provisioning. Re-run `app.db.roles provision` after every migration. See [ADR-0012](docs/ADR/0012-row-level-security.md).

On Windows, `--loop asyncio:SelectorEventLoop` is required. Async psycopg cannot use the default Windows event loop, and the API refuses to start without it (see [ADR-0005](docs/ADR/0005-async-sqlalchemy-psycopg.md)).

| Endpoint | Purpose | Healthy | Unhealthy |
|---|---|---|---|
| `GET /api/v1/health/live` | Process is running; checks no dependencies | 200 `{"status":"ok"}` | — |
| `GET /api/v1/health/ready` | PostgreSQL and Redis both reachable | 200 `{"status":"ready","checks":{…}}` | 503 `not_ready` and which check failed |
| `GET /api/v1/health/db` | PostgreSQL reachable (`SELECT 1`) | 200 `{"status":"ok"}` | 503 `{"status":"unavailable"}` |
| `GET /api/v1/health/redis` | Redis reachable (`PING`) | 200 `{"status":"ok"}` | 503 `{"status":"unavailable"}` |

Each dependency check times out after `HEALTH_CHECK_TIMEOUT_SECONDS` (default 2). The API starts even when PostgreSQL or Redis is down; only readiness reflects it.

- OpenAPI docs (development and test only): http://localhost:8000/docs
- Every response carries an `X-Request-ID` header. A safe client-supplied value (letters, digits, `._-`, at most 64 characters) is echoed back; anything else is replaced with a new UUID.
- Errors use one shape: `{"error": {"code", "message", "request_id", "details"}}`. Stack traces and exception text are never returned.
- Logs are JSON lines on stdout. Passwords, tokens, URL credentials and configured secrets are redacted.

### Authentication and workspaces

Browser sessions use cookies only. Tokens never appear in response bodies, and the frontend must not store them. The full design, including key rotation and the row-level security plan, is in [ADR-0007](docs/ADR/0007-authentication-sessions-and-rbac.md).

| Endpoint | Auth | Request body | Success | Notable errors |
|---|---|---|---|---|
| `POST /api/v1/auth/register` | — | `{email, password, workspace_name?}` | 201 session payload + cookies | 409 `EMAIL_ALREADY_REGISTERED`, 422 |
| `POST /api/v1/auth/login` | — | `{email, password}` | 200 session payload + cookies | 401 `INVALID_CREDENTIALS`, 429 `RATE_LIMITED` |
| `POST /api/v1/auth/refresh` | refresh cookie + CSRF | — | 200 session payload, rotated cookies | 401 `INVALID_REFRESH_TOKEN` / `REFRESH_TOKEN_REUSED` (cookies cleared) |
| `POST /api/v1/auth/logout` | CSRF if a session is present | — | 204, cookies cleared | 403 `CSRF_TOKEN_INVALID` |
| `GET /api/v1/auth/me` | access cookie | — | 200 `{user, memberships}` | 401 `AUTHENTICATION_REQUIRED` |
| `GET /api/v1/workspaces?limit&offset` | access cookie | — | 200 page of your workspaces with your role | 401 |
| `GET /api/v1/workspaces/{id}` | VIEWER+ | — | 200 workspace | 404 `WORKSPACE_NOT_FOUND` for non-members |
| `GET /api/v1/workspaces/{id}/members?limit&offset` | VIEWER+ | — | 200 page of members | 404 |
| `POST /api/v1/workspaces/{id}/invitations` | ADMIN+ and CSRF | `{email, role: ADMIN\|MEMBER\|VIEWER}` | 201 invitation incl. one-time `invitation_token` | 403 `INSUFFICIENT_ROLE` / `CANNOT_GRANT_ROLE`, 409 `ALREADY_A_MEMBER` |
| `POST /api/v1/invitations/accept` | access cookie and CSRF | `{token}` | 200 `{workspace_id, workspace_name, role}` | 404 `INVITATION_NOT_FOUND`, 409 |

The session payload is `{user, memberships, access_expires_at, session_expires_at}`.

**Cookies:**

| Cookie | Lifetime | Attributes |
|---|---|---|
| `agentflow_access` | 15 min | HttpOnly, SameSite=Lax |
| `agentflow_refresh` | rotated on every refresh | HttpOnly, SameSite=Strict, Path=`/api/v1/auth` |
| `agentflow_csrf` | session lifetime | readable by the frontend |

In production the names gain `__Host-` / `__Secure-` prefixes and every cookie is `Secure`.

Browser rules:
- Every POST/PUT/PATCH/DELETE must come from an origin listed in `CORS_ALLOWED_ORIGINS`, otherwise 403 `ORIGIN_NOT_ALLOWED`. Add `http://localhost:8000` there if you want to call unsafe endpoints from `/docs`.
- Authenticated unsafe requests, plus refresh and logout, must send the `agentflow_csrf` cookie value in `X-CSRF-Token`.
- Reusing an old refresh token ends the whole session, so the frontend must make sure only one refresh request is in flight at a time.

Roles run VIEWER < MEMBER < ADMIN < OWNER. Owners may invite ADMIN, MEMBER or VIEWER, and admins may invite MEMBER or VIEWER. Login attempts and the other authentication endpoints are rate-limited through Redis. If Redis is down they return 503 rather than run unprotected.

### Agent registry

All agent routes live under `/api/v1/workspaces/{workspace_id}`. Every workspace sees its own agents plus the six built-in specialists (Researcher, Analyst, Developer, Writer, QA, General). Built-ins are read-only. The design is in [ADR-0008](docs/ADR/0008-agent-registry-and-versioning.md).

| Endpoint | Min role | Body | Success | Notable errors |
|---|---|---|---|---|
| `GET /agents?status&limit&offset` | VIEWER | — | 200 page of agents | — |
| `POST /agents` | ADMIN | `{name, category, description?, configuration}` | 201 agent (INACTIVE, draft only) | 409 `AGENT_NAME_TAKEN`, 422 |
| `GET /agents/{agent_id}` | VIEWER | — | 200 agent | 404 `AGENT_NOT_FOUND` |
| `PATCH /agents/{agent_id}` | ADMIN | `{revision, name?, category?, description?, configuration?}` | 200 agent (draft updated) | 409 `AGENT_REVISION_CONFLICT`, 403 `AGENT_READ_ONLY` |
| `POST /agents/{agent_id}/publish` | ADMIN | `{revision}` | 201 new immutable version | 409 `NOTHING_TO_PUBLISH`, 403 `TOOL_REQUIRES_OWNER`, 422 |
| `POST /agents/{agent_id}/activate` / `deactivate` | ADMIN | `{revision}` | 200 agent | 409 `AGENT_NOT_PUBLISHED`, 403 `TOOL_REQUIRES_OWNER` |
| `GET /agents/{agent_id}/versions?limit&offset` | VIEWER | — | 200 page of versions, newest first | 404 |
| `POST /agent-proposals` | MEMBER | `{name, category, description?, rationale, configuration}` | 201 proposal (PENDING) | 422 |
| `GET /agent-proposals?status&limit&offset` | VIEWER | — | 200 page of proposals | — |
| `POST /agent-proposals/{id}/approve` | ADMIN, not the author | `{note?}` | 200 proposal; creates an INACTIVE agent with version 1 | 403 `PROPOSAL_SELF_REVIEW` / `TOOL_REQUIRES_OWNER`, 409 `PROPOSAL_ALREADY_DECIDED` |
| `POST /agent-proposals/{id}/reject` | ADMIN, not the author | `{note?}` | 200 proposal | as above |

**Configuration** (strict; unknown fields rejected):

```json
{
  "system_instruction_ref": "workspace://prompts/summarizer/v1",
  "model": {"profile": "default", "temperature": 0.2, "max_output_tokens": 4096, "credential_ref": null},
  "capabilities": ["report_writing"],
  "tools": [{"tool": "artifact_writer", "access": "write"}],
  "limits": {"timeout_seconds": 600}
}
```

Capabilities, tools and categories must come from the catalog in `backend/app/agents/catalog.py`. Tools that execute code (`python_analysis`, `test_runner`) are HIGH risk: only a workspace OWNER can publish, approve or activate a configuration that grants one. Credentials are never stored; `credential_ref` only accepts `secret://<name>`.

Rules to know:
- Published versions never change, and tasks keep the exact version they were assigned.
- Every mutating request must send the agent's current `revision`. A stale revision is rejected, so two editors can't overwrite each other.
- Approving a proposal does not activate the agent; activation is a separate step.

### Workflows and tasks

Routes live under `/api/v1/workspaces/{workspace_id}/workflows`. The lifecycle, DAG rules and the full transition matrix are in [ADR-0009](docs/ADR/0009-workflow-and-task-lifecycle.md).

| Endpoint | Min role | Body | Success | Notable errors |
|---|---|---|---|---|
| `POST /workflows` (optional `Idempotency-Key` header) | MEMBER | `{prompt, settings: {autonomy_policy?, budget_limit?, currency?, max_parallel_tasks?, allow_dynamic_agent_proposals?, deadline?}}` | 201 PENDING workflow; replays add `Idempotent-Replayed: true` | 409 `IDEMPOTENCY_KEY_REUSED`, 422 |
| `GET /workflows?status&limit&offset` | VIEWER | — | 200 page | — |
| `GET /workflows/{id}` | VIEWER | — | 200 workflow | 404 `WORKFLOW_NOT_FOUND` |
| `POST /workflows/{id}/transitions` | MEMBER (approve plan, unblock: ADMIN) | `{to_status, revision, reason?}` | 200 workflow | 409 `INVALID_TRANSITION` / `TRANSITION_PRECONDITION_FAILED` / `WORKFLOW_REVISION_CONFLICT` |
| `POST /workflows/{id}/start` · `/cancel` | MEMBER | `{revision, reason?}` | 200 (READY→RUNNING · →CANCELLED) | as above |
| `GET /workflows/{id}/events?after&limit` | VIEWER | — | 200 `{items, last_sequence}` in order | — |
| `GET /workflows/{id}/tasks?limit&offset` · `GET …/tasks/{task_id}` | VIEWER | — | 200 | 404 `TASK_NOT_FOUND` |
| `POST /workflows/{id}/tasks` | MEMBER | `{title, description?, required_skills?, input_references?, expected_output_schema?, acceptance_criteria?, priority?, required?, depends_on?}` | 201 task | 409 `PLAN_NOT_EDITABLE`, 422 |
| `PATCH …/tasks/{task_id}` | MEMBER | `{revision, …fields}` | 200 task | 409 `TASK_REVISION_CONFLICT` / `PLAN_NOT_EDITABLE` |
| `DELETE …/tasks/{task_id}?revision=` | MEMBER | — | 204 (kept as removed/CANCELLED history) | 409 |
| `POST …/tasks/{task_id}/dependencies` · `DELETE …/dependencies/{depends_on_task_id}` | MEMBER | `{depends_on_task_id}` | 201 task · 204 | 422 `INVALID_DEPENDENCY` / `DEPENDENCY_CYCLE`, 409 `DEPENDENCY_EXISTS` |
| `POST …/tasks/{task_id}/assign` | MEMBER | `{agent_id, revision}` | 200 task pinned to the agent's active version | 409 `AGENT_NOT_RUNNABLE` / `INVALID_TRANSITION`, 422 `AGENT_LACKS_SKILLS` |
| `POST …/tasks/{task_id}/cancel` | MEMBER | `{revision, reason?}` | 200 task | 409 |

A typical manual run looks like this:

1. Create the workflow.
2. Add tasks and dependencies.
3. `PENDING → PLANNING`.
4. Submit the plan with `→ AWAITING_APPROVAL`, or go straight to `→ READY` when `autonomy_policy` is `autonomous`.
5. An ADMIN approves with `→ READY`.
6. `start`. Tasks whose dependencies are complete become READY automatically.
7. Assign an agent.

Queueing, execution and verification arrive in later steps. The workflow completes by itself once every required task is COMPLETED. Editing a submitted or approved plan sends it back to PLANNING. Budgets use `budget_limit` (decimal string, up to 4 places) and a 3-letter `currency`; `max_budget` is not accepted.

### Database and migrations

The schema is managed by Alembic (`backend/alembic/`). Migrations read the database URL from the repo-root `.env`. Run them from `backend/`:

```powershell
uv run alembic upgrade head               # apply all migrations
uv run alembic current                    # show the applied revision
uv run alembic downgrade -1               # revert the last migration (local databases only)
uv run alembic check                      # fails if the models and migrations have drifted
uv run alembic revision --autogenerate -m "describe change"   # draft a new migration, then review it by hand
```

| Table | Purpose |
|---|---|
| `users` | Accounts (email unique case-insensitively; only password hashes are stored) |
| `workspaces`, `workspace_members` | Tenants and membership with roles OWNER / ADMIN / MEMBER / VIEWER |
| `agents`, `agent_versions` | Agent definitions with draft and revision (`workspace_id` NULL = built-in) and immutable published versions (`UPDATE` rejected by a trigger) |
| `workflows`, `workflow_tasks`, `task_dependencies` | Requests (10 statuses, `budget_limit`, `revision`), their tasks (required/removed flags, pinned agent version) and the acyclic dependency graph |
| `workflow_events` | Append-only history (`UPDATE` is rejected by a trigger), ordered by `sequence` |
| `idempotency_keys` | Stored responses for retried requests; expire after `IDEMPOTENCY_KEY_TTL_SECONDS` (default 24 h) |
| `auth_sessions`, `refresh_tokens` | Login sessions (refresh-token families) and single-use refresh tokens, stored as SHA-256 hashes only |
| `workspace_invitations` | Pending and accepted invitations; token stored as a hash; role cannot be OWNER |
| `agent_proposals` | Proposed agents awaiting review; content frozen after submission |
| `agent_audit_events` | Append-only history of every registry change |
| `outbox_events`, `inbox_messages` | Transactional outbox (identifiers only, leases, attempts) and consumed event IDs for idempotent workers |
| `agent_executions` | One row per task attempt: agent version, status, timing, safe error or result summary |

Workspace isolation is enforced by the database: child rows carry `workspace_id`, and composite foreign keys stop them pointing into another workspace or workflow. Deleting a workspace deletes all of its data. Conventions, deletion rules and the idempotency design are in [ADR-0006](docs/ADR/0006-core-schema-and-idempotency.md).

Checks:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy app tests alembic
uv run pytest                             # all tests; integration tests need the infrastructure running
uv run pytest -m "not integration"        # unit tests only, no infrastructure needed
```

Integration tests create throwaway databases named `agentflow_test_*` on the local server, migrate them, and drop them afterwards. Your development database is never touched.

## Model gateway

Every language-model call goes through one gateway in the Celery workers (`app/llm/gateway.py`, [ADR-0013](docs/ADR/0013-model-gateway.md)), and every call is recorded in `llm_usage` with the token counts the provider reported. Costs stay unpriced until Phase 7. The only provider so far is a deterministic mock (`MODEL_PROVIDER=mock`, `MODEL_NAME=mock-1`), so no API key is needed; real providers arrive behind LiteLLM in Phase 3.

**Prompt analysis** ([ADR-0014](docs/ADR/0014-prompt-analysis.md)). When planning starts, a worker analyses the workflow's goal into an objective, requirements, constraints, deliverables, assumptions and open questions. The result is shown on the workflow's Requirements tab. Failed analyses can be retried; open questions are informational until clarification requests arrive (Phase 6).

**Plan drafts** ([ADR-0015](docs/ADR/0015-plan-drafts.md)). A ready analysis is turned into a draft plan: at most 25 small tasks with skills, deliverables, acceptance criteria and dependencies. Each draft is an immutable version, and members can regenerate it. Drafts never change a workflow's tasks until one is confirmed. The **Plan draft** tab previews any version by stage, with validation: dependency cycles are errors, and skills no active agent provides, optional prerequisites and over-wide stages are warnings ([ADR-0016](docs/ADR/0016-plan-dag-validation-and-preview.md)).

## Background workers (Celery)

Workflow tasks are executed by Celery workers, fed through a transactional outbox: the API only writes outbox rows, and the relay publishes them to Redis. In this step the worker runs a harmless mock agent; there is no LLM call. The design is in [ADR-0010](docs/ADR/0010-background-execution-outbox-and-workers.md).

One-time setup, after `alembic upgrade head`: set `WORKER_DB_PASSWORD` (and `API_DB_PASSWORD`) in `.env`, then run `uv run python -m app.db.roles provision`. Re-run the provisioning after future migrations.

Run each in its own terminal from `backend/`, with the infrastructure up:

```powershell
# API (port 8000 is used here by default; pick another if it is taken)
uv run uvicorn app.main:create_app --factory --port 8000 --loop asyncio:SelectorEventLoop

# Worker: Windows needs the solo pool
uv run celery -A app.worker.celery_app worker --pool=solo -Q execution,maintenance --loglevel=INFO

# Beat: exactly one instance. It runs the outbox relay every 5 s and cleanup hourly.
uv run celery -A app.worker.celery_app beat --schedule=celerybeat-schedule --loglevel=INFO
```

| Queue | Tasks |
|---|---|
| `execution` | `agentflow.execute_task`: one attempt of a dispatched task |
| `maintenance` | `agentflow.relay_outbox` (every 5 s), `agentflow.cleanup` (hourly, at minute 0) |

**Linux production:**
- Run prefork workers, for example `celery -A app.worker.celery_app worker -Q execution --concurrency 4` and a separate `-Q maintenance --concurrency 1` worker.
- Run exactly one Beat process.
- Run everything as a non-root user, with `WORKER_DB_PASSWORD` and the broker password supplied by the environment or a secret manager.

Task time limits (600 s soft, 660 s hard) are enforced only by prefork workers, not by the Windows `solo` pool.

How delivery behaves:
- Delivery is **at-least-once**, and workers deduplicate on the stable event ID.
- A task attempt ends `SUBMITTED`; verification arrives in Phase 5.
- On failure, a task becomes `REWORK_REQUIRED`, or `FAILED` after the 3rd attempt.
- Each attempt is kept in `agent_executions`.

## Frontend

The web application (ADR-0011): sign-in, workspaces, dashboard, workflows (planning, dependencies, assignment, lifecycle, live execution monitoring), agents and versions, agent proposals, members and invitations.

```powershell
cd frontend
npm ci
npm run dev                               # http://localhost:3000
```

- **API address.** The app calls the API at `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`). To use another port, put it in `frontend/.env.local` (gitignored), for example `NEXT_PUBLIC_API_BASE_URL=http://localhost:8010`. It is a public, build-time value: never put secrets in `NEXT_PUBLIC_*` variables.
- **Allowed origins.** The API must allow the app's origin (`CORS_ALLOWED_ORIGINS`). The default allows `http://localhost:3000` and `http://127.0.0.1:3000`.
- **Use the same host name for both.** Open the app on the same host name as the API (`localhost` with `localhost`). The app must read the API's CSRF cookie, and cookies are per host. In production, serve both from one host, for example with a reverse proxy routing `/api` to the API.
- **No authentication tokens in the browser's reach.** They stay in HttpOnly cookies and never reach JavaScript, `localStorage` or `sessionStorage`.
- **Live updates use polling.** There is no push endpoint. The workflow page polls the event log while work is running.

Checks:

```powershell
npm run lint
npm run typecheck
npm test                                  # Vitest + React Testing Library
npm run build
```

### End-to-end tests (Playwright)

The journeys run against the real stack. No AI API is called: the mock agent executes tasks.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/infra.ps1 up
cd frontend
npm run test:e2e
```

What the run does:
- **Database.** It resets a disposable database, `agentflow_e2e`, then migrates it, seeds the built-in agents and provisions the API and worker roles, so the API runs under row-level security as in production. It refuses any database name not ending in `_e2e`.
- **Processes.** It starts the API on :8011, a Celery worker and Beat (Redis databases 2 and 3), and a production build on :3100. All of them are stopped afterwards.
- **Logs.** Backend logs go to `frontend/test-results/e2e-backend/`.
- **Browser.** It uses the installed Google Chrome. Set `PLAYWRIGHT_CHANNEL=""` to use Playwright's Chromium instead (`npx playwright install chromium`).

## All checks

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check.ps1        # add -E2E to include Playwright
```

## Continuous integration

GitHub Actions runs the same gates on pushes to `main` and on pull requests:
- **`backend-ci`** (`.github/workflows/backend-ci.yml`), on Ubuntu with PostgreSQL 18.6 and Redis 8.10.3 containers. It runs `uv sync --locked`, ruff, mypy, migrations with seeding and role provisioning, `alembic check` and pytest.
- **`frontend-ci`** (`.github/workflows/frontend-ci.yml`), on Node 24. It runs `npm ci`, lint, typecheck, Vitest and the production build.

The Playwright journeys need the whole stack and run locally with `check.ps1 -E2E`.

## Configuration

Copy `.env.example` to `.env` for local values. Never commit `.env` or secrets. The backend reads the repo-root `.env`; real environment variables take precedence over it. Optional backend settings and their defaults are listed (commented) in `.env.example`. `APP_ENV=production` disables the API docs and refuses to start with placeholder passwords.
