# AgentFlow Progress

## Current position
- Phase: 2 — Intelligent planning (Phase 1 released as `v0.1.0`, pushed with its tag)
- Last completed step: 2.3 — Plan drafts: task decomposition and acceptance criteria
- Next step: 2.4 — DAG validation and plan preview API, which needs a short plan and the owner's go-ahead

## Completed steps
| Step | Commit message | Hash |
|---|---|---|
| 1.0 | `docs: establish AgentFlow implementation plan` | `16a9b59` |
| 1.1 | `chore: initialize AgentFlow monorepo` | `80e28ce` |
| 1.2 | `infra: add local PostgreSQL and Redis` | `9f8a550` |
| 1.3 | `feat(backend): establish API and infrastructure foundation` | `44eef4a` |
| 1.4 | `feat(database): add core AgentFlow schema and migrations` | `9543279` |
| 1.5 | `feat(auth): add authentication and workspace RBAC` | `7eded85` |
| 1.6 | `feat(agents): implement agent registry and versioning` | `baaedb9` |
| 1.7 | `feat(workflows): implement workflow and task management` | `82483e6` |
| 1.8 | `feat(execution): add reliable background execution with Celery` | `51e03f5` |
| 1.9 | `feat(frontend): add AgentFlow web application` | `b3cce86` |
| 1.9a | `feat(security): enable PostgreSQL row-level security` | `889d989` |
| 1.10 | `release: complete AgentFlow foundation v0.1.0` | `04f1bc1` (tag `v0.1.0`) |
| — | `ci: isolate unit-test settings from the environment and update actions` | `54dc11b` |
| — | `ci: pin astral-sh/setup-uv to v10.3.3` | `19f2604` |
| 2.1 | `feat(llm): add model gateway with usage records and mock adapter` | `d2aef8f` (pushed, CI ✔) |
| 2.2 | `feat(planning): add structured prompt analysis` | `8dffe47` (pushed, CI ✔) |
| 2.3 | `feat(planning): add versioned plan drafts with task decomposition` | latest commit touching `backend/app/planning/plan.py` |

## Environment snapshot (2026-11-12)
| Item | Value |
|---|---|
| Repo | `C:\Users\shivramm\project\AgentFlow`, branch `main`; pushed to `origin` up to `8dffe47` plus tag `v0.1.0` (owner-authorized) |
| Python / uv / Node / npm | 3.12.14 via uv 0.12.9 / Node 24.13.0 / npm 11.6.2 |
| Celery / kombu | 5.7.0 / 5.7.0 |
| Docker | 29.8.2, Compose v5.6.0 — `agentflow` stack healthy (Docker Desktop must be started first) |
| Dev database | `agentflow` at `0009 (head)`; built-ins seeded; `agentflow_api` and `agentflow_worker` provisioned |
| Local `.env` | holds generated `WORKER_DB_PASSWORD` and (new) `API_DB_PASSWORD` (gitignored) |
| Port 8000 | Used by another local project (`easa`); smoke tests used 8010 |
| E2E | API :8011 (as `agentflow_api`), frontend :3100, database `agentflow_e2e` (reset each run), Redis DBs 2/3, installed Google Chrome |
| Frontend audit | `npm audit --omit=dev`: 0 vulnerabilities; dev-only: 8 moderate, all the existing `minimatch` chain via ESLint |
| Test HTTP client | `httpx2` 2.16.0 (replaces `httpx`, OD-10) |

## Step 2.3 — plan drafts (ADR-0015)
- **Owner decisions (2026-11-13):** generation starts automatically when the analysis is READY; versioned drafts, applied only on confirmation (2.5); a minimal status UI; at most 25 tasks.
- **Trigger:** a ready analysis calls `engine.request_plan` in the same transaction (`workflow.plan` outbox, `plan_status=PENDING`, `plan_request_id`, `PLAN_REQUESTED`). `POST …/plan-drafts` (Member) regenerates; otherwise 409 `PLAN_NOT_REGENERATABLE`.
- **`app/planning/plan.py`:**
  - the `PlanProposal`/`PlannedTask` schema: catalog skills; deliverables; acceptance criteria with unique IDs; known, non-self dependencies; unique keys; 1–25 tasks. It fits `TaskCreateRequest`.
  - the prompt `plan_generation/v1`, with the analysis and the request delimited;
  - a deterministic mock: parallel tasks per requirement, then a consolidation task;
  - the job: claim → generate (3 attempts) → finish (next immutable draft version, or failure).
- **Celery:** `agentflow.plan_workflow`.
- **Migration `0009`:** `workflow_plans` (immutable via trigger, RLS, version unique per workflow); `workflows.plan_status` and `plan_request_id`; three event types.
- **Grants:** the worker has `SELECT, INSERT` on `workflow_plans`.
- **API:** `plan_status` on the workflow; `GET …/plan-drafts` lists draft summaries.
- **Frontend:** `plan-draft-status.tsx` in the Requirements tab (drafting / failed / ready, with Regenerate for members); polling while pending.
- **Deferred:** cycle detection and the full draft preview are Step 2.4.

## Checks last executed (Step 2.3)
- **`scripts/check.ps1 -E2E` ✔:**
  - ruff; mypy (128 files); alembic check;
  - pytest **814 passed** (786 plus 28 new: 21 unit, 7 integration);
  - eslint, typecheck; Vitest **85** (3 new);
  - Playwright **14 passed**: journey 7 checks that the real worker produces draft v1;
  - build.
- **One test updated for the new chaining:** the analysis test now also expects `PLAN_REQUESTED` after `REQUIREMENTS_ANALYZED`.
- **Deliberate-bug check** (reverted): skipping the unknown-dependency validation makes 2 tests fail.
- **Environment:** Docker Desktop had stopped after the restart; it was started again. The dev DB was migrated to `0009` and re-provisioned. No E2E processes were left running.

## Step 2.2 — structured prompt analysis (ADR-0014)
- **Owner decisions (2026-11-12):** analysis starts automatically when planning starts; bounded retries, then a recorded failure with a user retry; ambiguities are recorded and shown, not blocking; a frontend Requirements panel.
- **Trigger:** `engine.request_analysis` runs on PENDING → PLANNING. In the same transaction it writes a `workflow.analyze` outbox row (identifiers only), sets `analysis_status=PENDING` and `analysis_request_id`, and appends `ANALYSIS_REQUESTED`.
- **`app/planning/analysis.py`:**
  - the strict `PromptAnalysis` schema: unique IDs, catalog-checked skills, credential-like text rejected;
  - the versioned prompt `prompt_analysis/v1`, with the request delimited and settings given as context;
  - a deterministic mock default derived from the request;
  - the job: `claim` (inbox, current request only) → `analyse` (gateway, 3 attempts with 1 s/2 s backoff) → `finish` (READY with `REQUIREMENTS_ANALYZED`, or FAILED with `ANALYSIS_FAILED`; superseded requests ignored).
  - Analysis never changes the workflow's `revision`.
- **Worker:** the Celery task `agentflow.analyze_workflow` (soft limit 300 s; a timeout is recorded as a failure). Outbox route `workflow.analyze`.
- **API:** `WorkflowResponse.analysis_status` and `normalized_requirements`; `POST …/workflows/{id}/analysis` (Member) retries; otherwise 409 `ANALYSIS_NOT_RETRYABLE`.
- **Migration `0008`:** `workflows.analysis_status` and `analysis_request_id`, plus three event types in the event-type check. Downgrade deletes those events first.
- **Mock adapter:** defaults may be functions of the request (`register_default`).
- **Frontend:** a Requirements tab (`requirements-panel.tsx`) showing pending, failed with retry for members, or the analysis as plain text; polling while pending; timeline shows error codes.
- **Tests changed because of new behaviour:**
  - the event-sequence test now expects `ANALYSIS_REQUESTED`;
  - one test selects the dispatch event by type;
  - the dispatch-focused `_world` helper removes the analysis outbox row, so every existing relay assertion stays exact.

## Checks last executed (Step 2.2)
- **`scripts/check.ps1 -E2E` ✔ ("All checks passed"):**
  - ruff; mypy (123 files); alembic check;
  - pytest **786 passed** (761 plus 25 new: 18 unit, 7 integration);
  - eslint, typecheck; Vitest **82 passed** (5 new);
  - Playwright **14 passed**: journey 7 now checks that the real worker's analysis appears on the Requirements tab;
  - build.
- **Deliberate-bug check** (reverted): removing the request-ID guard makes `test_a_stale_delivery_changes_nothing` fail.
- **No E2E processes were left running.** The dev DB was migrated to `0008` and re-provisioned.

## Step 2.1 — model gateway (ADR-0013)
- **Owner decisions (2026-11-12):** usage is persisted from the start (`llm_usage`); models are called from workers only, through a sync gateway; the mock is scripted, with deterministic defaults.
- **`app/llm/`:**
  - `types.py`: `ModelRequest`, `ChatMessage`, `CallContext`, `ProviderUsage`, the `ModelAdapter` protocol and `ModelResponse`.
  - `mock.py`: `MockModelAdapter`, with per-purpose scripts that may raise; `DEFAULT_RESPONSES` (empty until Step 2.2 registers purposes); deterministic usage and request IDs.
  - `gateway.py`: `ModelGateway.complete()` resolves the model (profile `default` → `MODEL_NAME`), validates structured output, records every call, and logs metadata only. `get_model_gateway()` gives one gateway per worker process.
- **`app/services/usage.py`:** writes each record in its own transaction, so usage survives the caller's rollback.
- **Migration `0007`:** the `llm_usage` table with composite FKs; checks for all-or-nothing costs, error code exactly when unsuccessful, non-negative tokens and task-implies-workflow; RLS tenant and platform policies.
- **Roles:** worker grant `SELECT, INSERT` (cannot rewrite history).
- **Settings:** `MODEL_PROVIDER` (`mock` only) and `MODEL_NAME` (`mock-1`).
- **Not yet called by any product code:** Step 2.2 will be the first caller (prompt analysis as a background job). No LiteLLM yet (Phase 3); no pricing (Phase 7).

## Checks last executed (Step 2.1)
- **Backend:** ruff format/check; mypy (118 files); alembic check; pytest **761 passed** (739 plus 22 new: 13 unit, 9 integration).
- **Frontend:** eslint, typecheck, Vitest 77, `next build`.
- **Playwright:** **14 passed** (4.2 min).
  - The first gate run had 1 failure, caused by the environment: every local log (API, worker, Redis connection) stalled together for about 21 minutes, with no errors, and the worker reported that Redis did not reply for 1263 seconds. This looks like the machine or Docker Desktop pausing.
  - A rerun passed fully.
- **Deliberate-bug check** (reverted): not recording failed calls makes 2 tests fail, one unit and one integration.
- **GitHub CI after the v0.1.0 push:**
  - `backend-ci` first failed on GitHub only. Unit-test settings read CI's exported environment variables (`WORKER_DB_PASSWORD`).
  - Reproduced exactly in Linux containers and fixed in `54dc11b`: unit settings are now built from constructor arguments only.
  - `setup-uv@v10` has no moving major tag, so it is pinned in `19f2604`.
  - Now ✔: `backend-ci` at `19f2604`, `frontend-ci` at `54dc11b`.

## Step 1.10 — release v0.1.0
- **CI:**
  - `.github/workflows/backend-ci.yml`: Ubuntu, a PostgreSQL 18.6 service, and Redis 8.10.3 with a password started in a step. It runs `uv sync --locked`, ruff, mypy, migrate/seed/provision, `alembic check` and pytest.
  - `.github/workflows/frontend-ci.yml`: Node 24, `npm ci`, lint, typecheck, Vitest, build.
  - Both have `contents: read` permissions and concurrency groups.
- **Release notes:** `CHANGELOG.md`; README status, layout and CI section updated.
- **CI rehearsal, without a push** (owner decision), running both workflows' commands in Linux containers against the local infrastructure:
  - **backend** (`ghcr.io/astral-sh/uv:python3.12-bookworm`): `uv sync --locked`, ruff, mypy, `alembic check`; pytest **738 passed, 1 skipped** (the Windows-only event-loop test).
  - **frontend** (`node:24-bookworm`, Node 24.22.3), on a container-local copy: `npm ci`, eslint, typecheck, Vitest **77 passed**, `next build`.
  - **It found a real Linux-only failure,** now fixed: mypy rejected `asyncio.ProactorEventLoop` in `tests/unit/test_lifecycle.py`. The call is now under a `sys.platform == "win32"` check, and `mypy --platform linux` is clean.
  - The first backend attempt hit a network timeout downloading a package; a retry with `UV_HTTP_TIMEOUT=180` passed.
- **Verified on GitHub after the push:** see Step 2.1 above; both workflows are green after two CI fixes.
- **Full stack from a clean database:** the Playwright harness resets `agentflow_e2e`, migrates from empty, seeds and provisions, then runs the API (restricted role, RLS), the Celery worker and Beat, PostgreSQL, Redis and a production frontend build. Its journeys cover registration and login, workspace isolation and permissions, agent CRUD, workflow and task CRUD, and mock-worker execution.

## Checks last executed (Step 1.10)
- **`scripts/check.ps1 -E2E` ✔ (exit 0, "All checks passed"):**
  - ruff, mypy, alembic check; pytest **739 passed**, no warnings;
  - eslint, typecheck; Vitest **77 passed**;
  - Playwright **14 passed**; `next build`.
- **Linux CI rehearsals:** both ✔, as above.

## Current implementation state (Step 1.9a)
- **ADR-0012:** resolves OD-13, OD-15 and OD-10, and opens OD-20.
- **Roles (`app/db/roles.py`):**
  - The API connects as `agentflow_api` (`API_DB_USER`/`API_DB_PASSWORD`): NOSUPERUSER, NOBYPASSRLS, NOINHERIT, owns nothing.
  - It has SELECT/INSERT/UPDATE/DELETE on application tables, but not on `alembic_version` or `inbox_messages`.
  - `POSTGRES_USER` is the administrator only: migrations, `app.agents.seed`, provisioning.
  - `uv run python -m app.db.roles provision` provisions both roles; re-run it after migrations.
- **Group roles:** `agentflow_rls_tenant` (the API) and `agentflow_rls_platform` (workers) are NOLOGIN with no privileges. Membership is granted `WITH INHERIT TRUE`, and policies target only these groups.
- **Migration `0006`:**
  - `ENABLE` and `FORCE` RLS on 13 workspace-owned tables;
  - functions `app_current_user_id()`, `app_user_workspace_ids()` (SECURITY DEFINER) and `app_can_join_workspace()`;
  - tenant policies plus read-only built-in agents and versions;
  - the workspace owner visible during registration;
  - joining a workspace only as its owner or with a valid invitation;
  - a platform policy for workers;
  - OD-15: the `uq_agents_workspace_id_lower_name` index.
- **Per-transaction context (`app/db/session.py`):**
  - `TenantSession` with an `after_begin` hook, plus `set_session_user()`, which runs `set_config('app.user_id', …, true)`.
  - It is set by authentication, by registration after creating the user, and by the session responses before reading memberships.
- **Service fix:** `_lock_owned` checks for built-ins before `FOR UPDATE`. RLS blocks locking rows outside the UPDATE policy, so built-ins keep their 403 `AGENT_READ_ONLY` instead of turning into 404.

## Checks last executed (Step 1.9a)
- **`scripts/check.ps1 -E2E` ✔ (exit 0, "All checks passed"):**
  - ruff format/check; mypy (109 files); alembic check clean;
  - pytest **739 passed**, with **no warnings** (the `httpx` deprecation is gone);
  - frontend eslint, typecheck; Vitest **77 passed**;
  - Playwright **14 passed**, with the E2E API running as the restricted role under RLS;
  - `next build`;
  - no E2E processes were left running.
- **Every API integration test runs as a provisioned restricted role** (`agentflow_api_it`), so the whole API suite runs under RLS.
- **New tests:**
  - **`test_row_level_security.py`** (10 tests):
    - RLS is enabled and forced on exactly the expected tables;
    - the API role's attributes, ownership, groups and excluded tables;
    - cross-tenant reads, inserts, updates, deletes and moves with **no application filter**;
    - no rows without a context, while built-ins stay readable;
    - built-ins are read-only;
    - no leftover context on the same pooled connection after commit or rollback;
    - joining a workspace only by invitation;
    - policies target only the group roles;
    - the case-insensitive name index.
  - **`test_agents_service.py`:** four concurrent case-variant creates leave exactly one agent; the others get `AGENT_NAME_TAKEN`.
  - **`test_config.py`:** the API URL, the required password, and the production placeholder rejection.
- **Migration stairway, downgrade to base and rollback-reapply** pass with `0006`. The dev DB was upgraded to `0006`.
- **Deliberate-bug checks** (each reverted afterwards), both caught:
  - roles provisioned with `BYPASSRLS`: 7 of 10 RLS tests fail;
  - the authentication context removed: 48 API test errors.

## Deviations and findings
- **Schema owner.** It stays the administrator (`POSTGRES_USER`), not ADR-0007's planned non-superuser owner; tracked as OD-20. Applications never connect as it.
- **Worker policy.** It is unscoped: the relay and cleanup are cross-tenant. The worker is still limited by its table grants (ADR-0010).
- **Context storage.** The user is kept on `session.info`, not in a contextvar.
- **Tables outside RLS,** as ADR-0007 designed: `users`, `auth_sessions`, `refresh_tokens`, `workspace_invitations`, `inbox_messages`.
- **Pre-existing flaky test fixed.** `test_tokens.py` built its claims at collection time. Once the suite passed 5 minutes, the `nbf` case became valid, and every other case was being rejected for expiry rather than its own reason. Claims are now built when each test runs, and each case first checks that a baseline token is valid.

## Known issues / limitations
- **OD-20:** a non-superuser schema owner for migrations, before any hosted deployment.
- **OD-14:** trusted proxy IP handling, before hosted deployment.
- **OD-19:** the hosted frontend must share a host with the API (CSRF cookie readability); a CSP with nonces is not set yet.
- **OD-17:** a worker crash after claim leaves the task IN_PROGRESS; recovery is Phase 4.
- **OD-18:** FAILED outbox rows are kept but not surfaced or replayable yet (Phase 4).
- **OD-16:** replanning for tasks blocked by a cancelled dependency.
- **Celery:** time limits are not enforced by the Windows `solo` pool, only by Linux prefork. Exactly one Beat must run. Dispatch latency is up to 5 s.
- **Polling:** live updates poll, since there is no SSE endpoint. The dashboard makes 13 count requests, and Activity covers only the 10 newest workflows.
- **Still open:** the dev-only `minimatch` finding and the Redis licence review.

## Blockers
None.

## Next exact action
1. **Push Step 2.3** on the owner's go-ahead, and confirm CI on GitHub.
2. **Step 2.4: DAG validation and plan preview API.** Present a short plan first. It covers cycle detection and the full draft preview (API and UI).
3. **Before any hosted deployment:** settle OD-14, OD-19 and OD-20.
