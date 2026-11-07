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
- PostgreSQL is authoritative state; Redis is transport/cache only. Dispatch uses a transactional outbox.
- Agents submit results; only platform verification accepts a task. LLM output is untrusted until validated.
- All LLM calls go through one model gateway that records usage. Never label estimated cost/tokens as actual.
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
- Run all gates: `powershell -ExecutionPolicy Bypass -File scripts/check.ps1`.
- Docker Desktop must be running for PostgreSQL/Redis:
  `docker compose -f infrastructure/docker/docker-compose.yml up -d | ps | logs | down`
- Never remove volumes (`down -v`) without explicit user confirmation.

## Quality gates
- Backend: `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy app tests`, `uv run pytest`.
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
