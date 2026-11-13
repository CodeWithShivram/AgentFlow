# AgentFlow — Project Rules for Claude Code

Detailed specification: `AGENTFLOW_HANDOFF.txt` (source of truth unless an accepted ADR in `docs/ADR/` supersedes it).
Current state: `progress.md`. Step list and status: `IMPLEMENTATION_PLAN.md`.

## Session start
1. Confirm repo root is `C:\Users\shivramm\project\AgentFlow`.
2. `git status`, `git branch`, `git log --oneline -10`.
3. Read `progress.md` and the target step in `IMPLEMENTATION_PLAN.md`. Rediscover state from files and Git, not chat memory.
4. Present a short plan and blocking questions before editing non-trivial work.

## Scope
- One numbered step at a time. Do not implement future steps or excluded MVP features (handoff §2).
- No speculative abstractions, no empty placeholder folders, no new dependency without stating why.
- Do not silently change the stack (handoff §3) or invent versions, env vars, API fields or paths.

## Architecture boundaries
- Modular monolith: one backend codebase, separate API and Celery worker processes, one Next.js frontend.
- API routes validate HTTP and call services. Services own business rules and transactions. Repositories own queries.
- Every task/workflow status change goes through the central state transition service.
- Backend layout: `app/core/` (config, logging, request_id, errors, redis), `app/db/` (engine/session), `app/services/`, `app/api/v1/`.
  Settings come only from `app.core.config.Settings`; never read `os.environ` directly. Infra objects live on `app.state` and are injected with `Depends`.
- API database access is async (`AsyncSession` via `get_db_session`); sessions never auto-commit.
- Schema changes only via Alembic migrations in `backend/alembic/versions/`: autogenerate a draft, review it by hand,
  keep `uv run alembic check` clean, and add upgrade/downgrade coverage. Conventions: ADR-0006.
- Every workspace-owned table has `workspace_id NOT NULL` plus composite FKs; every repository query takes `workspace_id`.
- Idempotent operations use `IdempotencyService.begin()` and `complete()`/`fail()` in the same transaction as the business change.
- Expected client errors subclass `AppError` (`app/core/errors.py`) with a status code, an error code and a safe message.
- Auth (ADR-0007): cookie-only tokens (never in bodies or localStorage); routes needing a user take `CurrentPrincipal`;
  workspace routes take `Depends(workspace_role(<min role>))`, which returns 404 to non-members. Never trust a
  client-supplied workspace ID without it, and never put roles in tokens. Unsafe methods need an allowed Origin
  and `X-CSRF-Token`; tests use `make_client` (sends the allowed Origin) and send the CSRF cookie value.
- Store only hashes of refresh, CSRF and invitation tokens. Auth endpoints must call the Redis `RateLimiter` (fails closed).
- RLS is enabled (ADR-0012): the API connects as restricted `agentflow_api` (`API_DB_PASSWORD`); policies limit it to the
  workspaces of `app.user_id`, set per transaction by `set_session_user` (authentication does this). POSTGRES_USER is
  admin only (migrations, seed, provisioning). New workspace-owned tables need RLS policies in their migration; run
  `uv run python -m app.db.roles provision` after migrations. `SELECT ... FOR UPDATE` only reaches rows the UPDATE policy
  allows (built-ins are read-only). Keep `workspace_role` and scoped queries as the first layer.
- Agent registry (ADR-0008): never execute instructions or tools in the registry. Configurations go through the strict
  `AgentConfiguration`; tools and capabilities come only from `app/agents/catalog.py`. `agent_versions` is immutable;
  tasks pin `assigned_agent_version_id`. Mutations take the agent `revision`. HIGH-risk tools need OWNER.
  Built-ins come only from `builtin_agents.json` via `uv run python -m app.agents.seed`.
- No ORM relationships are defined: flush a parent row before inserting rows that reference it in the same transaction.
- Workflows (ADR-0009): only the engine (`app/workflows/engine.py`, async adapter `app/services/transitions.py`) changes workflow/task status, under the workflow row lock
  (`lock_for_change`), with the event written in the same transaction. The matrix lives in `app/workflows/state_machine.py`
  with an actor per edge; agents (WORKER) never reach COMPLETED. Use `budget_limit`, never `max_budget`.
- App sessions do not autoflush and must not use `populate_existing` on rows already changed in the transaction;
  service-level tests should use `AsyncSession(..., autoflush=False)` like production.
- Port 8000 may be used by another local project: run AgentFlow smoke servers on another port and stop them by PID.
- Background execution (ADR-0010): the API never calls the broker; dispatch is written to the outbox by
  `app/workflows/engine.py` in the same transaction. Workers use the sync engine (`app/db/sync_session.py`) as the
  restricted `agentflow_worker` role; consumers must be idempotent via `outbox.record_consumed`. Celery messages carry IDs
  only. New tables a worker touches need an entry in `app/db/roles.py` WORKER_GRANTS (then re-provision). Row locks
  need UPDATE privilege. Never claim exactly-once delivery.
- Errors use the standard shape in `app/core/errors.py`; never return exception text. Log through `logging` (JSON + redaction), never `print`.
- PostgreSQL is authoritative state; Redis is transport/cache only. Dispatch uses a transactional outbox.
- Agents submit results; only platform verification accepts a task. LLM output is untrusted until validated.
- All LLM calls go through `app/llm/gateway.py` (`get_model_gateway().complete(...)`, ADR-0013), in workers only; the
  API never calls a model. Each call writes an `llm_usage` row (its own transaction) with provider-reported usage; costs
  stay NULL until Phase 7 pricing. Validate structured output with a Pydantic `response_format`; never log prompts or
  outputs. Tests use `MockModelAdapter.script(...)`; register schema-valid mock defaults for new purposes. Never label
  estimated cost/tokens as actual.
- Planning jobs (ADR-0014, `app/planning/`) follow claim -> model call outside any transaction -> finish, keyed by a
  request id on the workflow so superseded deliveries change nothing. Prompts reach models only inside delimiters;
  outbox payloads and events carry identifiers and counts, never prompt text. Analysis and plan drafts never bump
  `revision`; drafts (`workflow_plans`, ADR-0015) are immutable versions and only become tasks on confirmation (2.5).
  Plan schema and DAG checks live in `app/planning/schema.py` and `dag.py` (ADR-0016), which import no gateway code,
  so the API may use them; drafts are always acyclic. Confirming a draft (ADR-0017, `tasks.confirm_plan_draft`) is a plan
  edit under the workflow lock and revision: errors block, warnings need acknowledgement, existing tasks are replaced
  (removed as history) or appended by explicit choice; it never starts work.
- Workflow events are append-only.

## Security
- Never commit `.env`, secrets, tokens, local DBs, logs, caches, `.venv`, `node_modules`. Only `.env.example` with placeholders.
- Argon2id for passwords. Enforce workspace authorization in backend services on every resource.
- No unrestricted shell or code execution by agents. Code execution stays disabled in Phase 1.
- Never return stack traces, secrets or raw exceptions to clients.

## Environment (Windows 11, PowerShell)
- Python baseline **3.12** (installed via uv; system `py` only has 3.14). Backend is a uv project:
  `cd backend; uv sync` creates `backend\.venv`; run tools with `uv run <tool>`. Add deps with `uv add` (never edit `uv.lock` by hand).
- Node 24 / npm 11. Use npm, not pnpm. `frontend/.npmrc` saves exact versions; use `npm ci` for clean installs.
- Next.js 16 differs from older versions: read `frontend/node_modules/next/dist/docs/` before writing Next code (see `frontend/AGENTS.md`).
- Run all gates: `powershell -ExecutionPolicy Bypass -File scripts/check.ps1` (backend integration tests need `scripts/infra.ps1 up`).
- Run API: `cd backend; uv run uvicorn app.main:create_app --factory --reload --port 8000 --loop asyncio:SelectorEventLoop`.
  The `--loop` option is mandatory on Windows (async psycopg; ADR-0005). There is no module-level `app`; use the factory.
- Docker Desktop must be running for PostgreSQL/Redis. Manage them with
  `powershell -ExecutionPolicy Bypass -File scripts/infra.ps1 <up|ps|logs|down|restart>` (reads repo-root `.env`).
- Never remove volumes (`down -v`, `infra.ps1 destroy-data`) without explicit user confirmation.

## Quality gates
- Backend: `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy app tests alembic`, `uv run alembic check`, `uv run pytest`.
- Frontend: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`.
- Never claim a check passed unless it ran and its output was inspected.
- Never weaken or delete tests to make them pass; explain and ask instead.

## Mandatory Git commit protocol
Every completed numbered step ends with exactly one commit. Before committing:
1. `git status`; review every changed file and the full diff (staged and unstaged).
2. Confirm no secrets, generated files, caches, `.env`, `.venv`, `node_modules` or unrelated user files.
3. Run format, lint, type checks, tests and a build/smoke check relevant to the step.
4. Confirm the step's acceptance criteria. Update `progress.md` and `IMPLEMENTATION_PLAN.md`.
5. Stage explicit paths only (no `git add -A`). One Conventional Commit, e.g. `feat(auth): add authentication and workspace RBAC`.
6. Inspect the commit and final `git status`. Report step, files, commands, results, limitations, hash, next step.

If checks fail or a blocker remains: no completion commit; mark the step BLOCKED in progress files and explain.
Commit locally only — **never push** unless the user asks. Never `--no-verify`, `reset --hard`, `clean -fd`, force-push or rewrite history.
Git identity is repository-local; do not change global Git config.
