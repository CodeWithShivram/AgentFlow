# AgentFlow Progress

## Current position
- Phase: 1 — Foundation
- Last completed step: 1.6 — Agent registry
- Next step: 1.7 — Workflow and task management (await explicit go-ahead; needs OD-5 and OD-11)

## Completed steps
| Step | Commit message | Hash |
|---|---|---|
| 1.0 | `docs: establish AgentFlow implementation plan` | `16a9b59` |
| 1.1 | `chore: initialize AgentFlow monorepo` | `80e28ce` |
| 1.2 | `infra: add local PostgreSQL and Redis` | `9f8a550` |
| 1.3 | `feat(backend): establish API and infrastructure foundation` | `44eef4a` |
| 1.4 | `feat(database): add core AgentFlow schema and migrations` | `9543279` |
| 1.5 | `feat(auth): add authentication and workspace RBAC` | `7eded85` |
| 1.6 | `feat(agents): implement agent registry and versioning` | latest commit touching `backend/app/services/agents.py` |

## Environment snapshot (2026-11-08)
| Item | Value |
|---|---|
| OS / shell | Windows 11 Pro, PowerShell 5.1 (Git Bash also available) |
| Repo root | `C:\Users\shivramm\project\AgentFlow`, branch `main`, remote `origin` = https://github.com/CodeWithShivram/AgentFlow.git (nothing pushed) |
| Python / uv / Node / npm | 3.12.14 via uv 0.12.9 / Node 24.13.0 / npm 11.6.2 |
| Docker | 29.8.2, Compose v5.6.0 — `agentflow` stack up |
| Dev database | `agentflow` at revision `0003 (head)`; six built-in agents seeded |
| Port 8000 | **In use by another project of the owner** (`C:\Users\shivramm\project\easa`). Run AgentFlow smoke tests on a different port and stop only processes started for them, by PID. |

## Current implementation state
- Steps 1.3–1.5: foundation, schema 0001–0002, idempotency, authentication, workspace RBAC.
- Migration `0003`:
  - `agents` gains `description`, `draft_configuration` and `revision`; statuses are now ACTIVE/INACTIVE, and ACTIVE requires a version.
  - `agent_versions` is immutable (trigger).
  - New `agent_proposals` table, with content frozen after submission and decisions final (trigger).
  - New append-only `agent_audit_events` table.
  - `workflow_tasks.assigned_agent_version_id`, with a composite FK to the agent's own version, `RESTRICT`, and a both-or-neither CHECK.
- `app/agents/`:
  - `catalog.py`: capabilities, categories, and tools with risk levels; HIGH = code execution, unavailable in Phase 1.
  - `builtin_agents.json`: Researcher, Analyst, Developer, Writer, QA, General.
  - `seed.py`: idempotent seeding, run with `uv run python -m app.agents.seed`.
- `app/schemas/agents.py`:
  - strict `AgentConfiguration` v1, holding an instruction reference and a model profile; `credential_ref` only accepts `secret://`;
  - credential-pattern scanning;
  - request and response models.
- `app/repositories/agents.py` (workspace-scoped visibility) and `app/services/agents.py` (lifecycle, revision locking, OWNER rule for HIGH-risk tools, proposals, audit, `resolve_runnable_version`).
- `app/api/v1/agents.py`: routes under `/api/v1/workspaces/{workspace_id}/agents` and `/agent-proposals`.
- `Base` now uses `eager_defaults=True`, so database-generated values come back via RETURNING.
- Test helpers for driving the API as a browser moved to `tests/integration/api_helpers.py`; the browser-client fixtures live in `tests/integration/conftest.py`.

## Checks last executed (Step 1.6)
- **Dev DB migrations:** `alembic upgrade` to 0003, `alembic check` clean, `downgrade -1` and re-upgrade ✔
- **Seeding the dev DB:** first run created all 6, the second run left all 6 unchanged ✔
- **`scripts/check.ps1` ✔:**
  - ruff format and check; mypy strict (79 files); alembic check;
  - pytest **310 passed** (168 unit, 142 integration);
  - eslint, typecheck, vitest 2, next build.
- **Deliberate-bug checks** (each reverted afterwards), all caught:
  - no OWNER rule for HIGH-risk tools: 2 failures;
  - proposer can review their own proposal: 1 failure. The DB CHECK still blocked it, with a 500;
  - approval activates the agent: 1;
  - workspace visibility filter removed: 1;
  - revision check removed: 1;
  - self-exclusion removed from the rename check: 1.

## Issues found and fixed during Step 1.6
- `MissingGreenlet` 500 after updates: database-generated `updated_at` was expired after flush. Fixed globally with `eager_defaults`.
- Audit rows inserted before their proposal row, an FK violation, because there are no ORM relationships to order inserts. Fixed by flushing parent rows first; recorded as a rule in CLAUDE.md.
- Case-only agent rename was rejected as `AGENT_NAME_TAKEN`. Fixed by excluding the agent itself from the name check; test added.
- **Existing tests made deterministic** (assertions unchanged):
  - `test_login_attempts_are_rate_limited` (Step 1.5) could straddle a fixed-window boundary under slow Argon2. The limiter clock is now pinned.
  - `test_readiness_ok` and `test_shutdown_…` (Step 1.3) timed out on a cold DB connection under 100% host CPU. Their integration settings now allow 10 s instead of the 2 s production default; the timeout path stays covered by unit tests.
- **Migration 0003 first failed** on a double-prefixed constraint name. A chained command then downgraded the dev DB to 0001 before re-upgrading. The tables were empty, so no data was lost. Fixed with `op.f()`; commands now run one step at a time.

## Known issues / limitations
- The fixed-window rate limiter can admit up to 2× the limit across a window boundary (documented; sliding windows are a possible later improvement).
- Agent name uniqueness is case-insensitive in the application, but the DB unique constraint is case-sensitive. A concurrent race could create "A" and "a".
- There is no read API for the agent audit trail yet; events are recorded and tested at the DB level.
- Proposals can only create new agents; changing an existing agent goes through draft and publish.
- Tool availability is not enforced by the registry; the tool gateway does that in Phase 3. HIGH-risk tools are unavailable in Phase 1.
- Carried over: RLS not enabled (OD-13, scheduled before 1.10); proxy IP handling (OD-14); `httpx2` deprecation warning (OD-10); dev-only `minimatch` audit finding; Redis 8 licence review; provisional workflow statuses (OD-11); cleanup jobs (OD-12).
- The live uvicorn smoke test for Step 1.5 auth is still not re-run (port 8000 belongs to another project; use a different port).

## Blockers
None.

## Open decisions
OD-5, OD-6, OD-8, OD-9, OD-10, OD-11, OD-12, OD-13, OD-14 in `IMPLEMENTATION_PLAN.md`. OD-5 (`max_budget` vs `budget_limit`) and OD-11 (workflow statuses) are needed for Step 1.7.

## Next exact action
On approval, with OD-5 and OD-11 decided, start Step 1.7:
- workflow create, list and detail; task CRUD and dependencies;
- DAG cycle validation;
- the central state-transition service;
- persisted workflow events;
- idempotent workflow creation through `IdempotencyService`;
- task assignment through `resolve_runnable_version`.
