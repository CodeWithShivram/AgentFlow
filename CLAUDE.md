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
- Agent runtime (ADR-0018, `app/agents/contract.py`, `base.py`): agents subclass `BaseAgent` (sync `execute`) and receive
  a `TaskContract` built by the runner from platform data only; they never touch the database or task status. Output
  must parse as `AgentSubmission` with exactly one claim per acceptance criterion (`validate_output` is platform-owned
  and `@final`); invalid output is a failed attempt (`INVALID_SUBMISSION`). Submissions are stored per attempt in
  `agent_executions.submission`; criterion claims are unverified. Never put parser or agent text in error summaries.
- Built-in runtimes (ADR-0021, `app/agents/specialists.py`): the runtime is chosen from the version's
  `system_instruction_ref` (`RUNTIMES`); others fail with `AGENT_RUNTIME_UNAVAILABLE`. Instruction texts are immutable
  versions in `app/agents/instructions.py` (change = new ref). The contract reaches models only inside
  `<task_contract>` delimiters, without platform IDs; one corrective retry names issue codes only. Agents call models via
  `context.models`; tests pass `models=` (and `MockAgent` explicitly where no model should run), never the global gateway.
- Developer and QA (ADR-0022) never execute code, run tests or write files (the platform applies accepted Developer
  proposals, ADR-0049): their outputs must fit `outputs_model`
  (`app/agents/outputs.py`: path-checked `proposed_files`/`proposed_tests`, `test_cases` covering contract criteria),
  and a runtime's `platform_note` is appended unless the contract grants the matching tool. Never drop those notes.
- Agent selection (ADR-0023): ranking is pure and versioned (`app/agents/selection.py`, `RULES_VERSION`); change
  weights or factors only with a new version. Unmeasured factors are `not_scored`, never guessed. Assignments always
  store a server-computed `agent_selections` row (append-only, RLS) in the same transaction; never trust a client
  rationale. People decide; the platform only suggests, except the bounded automatic reassignment of ADR-0032
  (`decided_by_user_id` NULL).
- Platform proposals (ADR-0024): only `agents.propose_specialists`, from plan confirmation when the workflow allows
  proposals and a task has no eligible agent. The template is fixed (task skills, `builtin://general/v1`, no tools);
  the platform never proposes tools or instructions. `requested_by` is NULL; approval stays ADMIN-only and creates an
  INACTIVE agent. Source links are frozen with the proposal.
- Tools (ADR-0019): agents call tools only via `context.tools.invoke(...)` (`app/tools/gateway.py`). A tool needs a
  `ToolDefinition` with an implementation in `app/tools/registry.py` (strict argument/result models) plus catalog policy;
  contracts list only usable grants. Calls outside the contract or over 50 per attempt are violations that fail the
  attempt even if the agent catches them. Each call writes a `tool_invocations` row (own transaction) with an argument
  hash only, never arguments or output. Handlers scope everything to their `ToolCallContext`; errors use fixed messages.
- All LLM calls go through `app/llm/gateway.py` (`get_model_gateway().complete(...)`, ADR-0013), in workers only; the
  API never calls a model. Each call writes an `llm_usage` row (its own transaction) with provider-reported usage and
  its cost (ADR-0040). Validate structured output with a Pydantic `response_format`; never log prompts or
  outputs. Tests use `MockModelAdapter.script(...)`; register schema-valid mock defaults for new purposes. Never label
  estimated cost/tokens as actual.
- Real models (ADR-0020): `MODEL_PROVIDER=litellm` uses `app/llm/litellm_adapter.py`, the only module that imports
  LiteLLM (it forces offline mode before import and routes LiteLLM logs through ours). Keys come only from
  `Settings.model_api_key`; LiteLLM retries stay off (`max_retries=0`, `num_retries=0`); never read LiteLLM's
  `response_cost`. Tests stay offline: pass `partial(litellm.completion, mock_response=...)`, never a real key.
- Planning jobs (ADR-0014, `app/planning/`) follow claim -> model call outside any transaction -> finish, keyed by a
  request id on the workflow so superseded deliveries change nothing. Prompts reach models only inside delimiters;
  outbox payloads and events carry identifiers and counts, never prompt text. Analysis and plan drafts never bump
  `revision`; drafts (`workflow_plans`, ADR-0015) are immutable versions and only become tasks on confirmation (2.5).
  Plan schema and DAG checks live in `app/planning/schema.py` and `dag.py` (ADR-0016), which import no gateway code,
  so the API may use them; drafts are always acyclic. Confirming a draft (ADR-0017, `tasks.confirm_plan_draft`) is a plan
  edit under the workflow lock and revision: errors block, warnings need acknowledgement, existing tasks are replaced
  (removed as history) or appended by explicit choice; it never starts work.
- Orchestration (ADR-0025, `app/orchestration/`): a LangGraph graph imported only by workers. Every status-changing
  transaction writes one `workflow.orchestrate` outbox row (`engine.request_orchestration`); the job assesses under the
  workflow lock and stores `workflows.next_step` without bumping `revision`. Nodes only read and decide; any node that
  acts must change state through the engine. Keep LangSmith tracing disabled (`langsmith.configure(enabled=False)`).
  Tests that count outbox rows must filter by event type.
- Checkpoints (ADR-0026): `PostgresSaver` on tables created by migration 0016 (never call its `setup()`); a library
  upgrade that changes its migrations needs a new Alembic migration first (pin test). Checkpoint tables are worker-only
  (no API grants, platform-only RLS); the job checkpoints on its own session connection (thread = workflow id), and the
  hourly cleanup keeps 20 per workflow.
- Outbox hardening (ADR-0027): raise `outbox.PermanentPublishError` for failures that can never succeed (no route,
  missing payload ids in `REQUIRED_PAYLOAD`), so the relay fails them at once. Failed jobs are retried only via
  `background_jobs.retry` (ADMIN, workflow lock, `BACKGROUND_JOB_RETRIED` event); clients see `error_type`, never
  `last_error`. Readiness includes the relay heartbeat in Redis; tests hitting `/health/ready` must write one.
- Scheduling (ADR-0028): only `engine._dispatch_waiting` writes task dispatches, for at most the workflow's free
  slots (`max_parallel_tasks` minus QUEUED/IN_PROGRESS and tasks already dispatched in the transaction) in
  `dispatch_order` (priority, then age); the worker claim still enforces the limit. Pre-assigned PENDING tasks keep
  their status; `reconcile_workflow` assigns them (SCHEDULER, `dispatch=False`) when ready, then decides once.
  No workspace-wide cap. Execution scenarios and test rules: `docs/TESTING.md`.
- Cancellation and recovery (ADR-0029): agents reach models and tools only through the runner's `AttemptGuard`
  (`is_current` check before each call; tests pass `is_current=` like `models=`). `app/execution/recovery.py`
  (Beat, every 60 s) is the only code that closes lost attempts (WORKER_LOST, automatic bounded retry), resends
  unclaimed dispatches and fails interrupted planning jobs; keep its thresholds above the worker time limits.
- Verification (ADR-0030): only `app/verification/job.py` (VERIFIER) accepts tasks; the engine writes `task.verify`
  whenever a task reaches SUBMITTED. Checks are pure and versioned in `app/verification/deterministic.py`
  (`VERIFIER`; change rules = new version) and never store agent text (codes, criterion ids from the task,
  deliverable names, schema locations only). `verification_results` is append-only.
- Semantic evaluation (ADR-0031, `app/verification/semantic.py`): passing checks request `task.evaluate`; the job
  is claim -> model call outside any transaction -> finish, and only `decide` (platform rules) turns findings into
  an outcome. Contract and submission reach the model only inside delimiters, without platform ids. Findings are
  untrusted model text: stored in `verification_results.findings`, shown as plain text, never logged or put in
  issues/events. An evaluation that cannot complete escalates the task (EVALUATION_UNAVAILABLE).
- Rework (ADR-0032, `app/verification/rework.py`, policy `retry/v1`): every failure that leaves a task
  REWORK_REQUIRED calls `schedule_rework` in the same transaction; it retries the same agent, reassigns once
  (two agent failures, another eligible agent), or waits for people (`NOT_RETRIED` policy codes). Corrective
  instructions are fixed platform sentences (criteria by id, deliverables by position, never user/agent/model
  text), stored on the attempt and sent to models after the delimited contract (contract v2).
- Replanning (ADR-0033): only PAUSED/BLOCKED workflows; the plan job drafts a `kind = "replan"` draft with a
  `basis` of completed tasks (`done-N` keys, `depends_on_completed`). Confirming keeps COMPLETED (and FAILED)
  tasks, removes the other unfinished ones as history, refuses in-flight work and stale bases, needs ADMIN for
  approval-required workflows, and never resumes work.
- Escalation and acceptance (ADR-0034): exhausted attempts and `NOT_RETRIED` policy failures go to ESCALATED,
  never FAILED; reassigning takes `attempt_count` past the highest recorded attempt. A RUNNING workflow past its
  deadline is paused once by the recovery sweep (DEADLINE_PASSED). COMPLETED needs PASSED deterministic and
  semantic results on the latest successful attempt: the engine checks it and a trigger (migration 0023) enforces
  it for any write, so tests complete tasks only through the real verification jobs (flush results first).
- Interventions (ADR-0035, `human_interventions`): only the engine opens/closes requests (`open_intervention`,
  `close_interventions`, hooks in the transition functions), once per `source_key`, with events carrying ids only;
  requested actions and contexts may be model/agent text (plain text, never logged). Members answer CLARIFICATIONs,
  ADMINs decide APPROVALs (`app/services/interventions.py`); escalations resolve by acting on the task (pass `user_id`
  in `extra`). Answers reach the planner only inside `<clarifications>`; approved agent actions only inside the
  contract (`approved_actions`). An agent's request counts as an attempt; the limit lives in `state_machine.py`.
- Pauses (ADR-0036): PAUSED always has `workflows.pause_reason` (USER, DEADLINE_PASSED, AWAITING_DECISION; check
  constraint, migration 0025); platform pauses pass `pause_reason=` to `transition_workflow`, people's transitions
  pass `user_id=`. Only `engine.resume_after_decisions` resumes automatically, and only AWAITING_DECISION pauses
  with no open agent request; never lift a person's or the deadline's pause.
- Expiry and rejection (ADR-0037): only agents' approval requests expire (`expires_at` from
  `Settings.intervention_approval_ttl_hours`, passed to `runner.finish`); the recovery sweep makes them EXPIRED and
  applies a rejection. Every decision on an agent request goes through `engine.decide_agent_action` (`stop` escalates).
  Rejection notes are for people: keep them out of events, logs, contracts and prompts.
- Approval audit (ADR-0038): every APPROVAL request has `required_role` (ADMIN; OWNER for agents granted HIGH-risk
  tools, `runner.approval_role`) and the service enforces it. Decisions write an immutable `approval_decisions` row
  via `engine.resolve_intervention` (pass `decided_role`); approved actions reach only the next attempt and only while
  the record's `action_fingerprint` matches the request.
- Pricing (ADR-0039): prices come only from `app/llm/pricing.json`, loaded as immutable snapshots by
  `uv run python -m app.llm.pricing load` (change a price = new `version`; never fetch prices online, never invent
  real prices). `record_model_call` links each call to the price in force (`model_price_id`) and sets `usage_source`;
  counts are `reported` only for providers the list marks `reports_usage`. Applications only read price tables.
- Costs and budgets (ADR-0040): `app.llm.costing` is the only cost arithmetic (decimals, 8 places, cached price or
  input price); `cost_basis` is actual/estimated/mock and they are never summed together. Unpriced calls stay NULL.
  Budgets count actual + estimated spend in the workflow's currency (no FX); `engine.pause_at_budget` pauses once
  (BUDGET_REACHED), called by the runner after each attempt and by the recovery sweep.
- Analytics (ADR-0041, `app/services/analytics.py`): aggregate only stored `llm_usage` rows (never invent metrics),
  per currency and cost basis; attribute agents through `agent_executions`, other calls as `platform:<purpose>`;
  every breakdown must add up to the overview. Ranges need a time zone and span at most 366 days.
- Timeline and report (ADR-0042): every `WorkflowEventType` needs a category in `app/workflows/event_categories.py`
  (and the frontend's `eventCategory`). The report (`app/services/report.py`) composes stored records only: outcomes and
  issue codes, never evaluator findings; no people's free text; deliverables as capped plain text. Platform calls
  (`analytics.PLATFORM_PURPOSES`) never count toward an agent, even when they record an attempt.
- Metrics (ADR-0043, `app/services/metrics.py`): computed in SQL from stored records; each measurement counts in the
  period it finished; rates always carry numerator and denominator and are null (never 0) when unmeasured; cancelled
  attempts stay out of failure rates; distributions report samples and exact `percentile_cont`.
- Task graph (ADR-0044, `frontend/features/workflows/graph.ts`, `dependency-graph.tsx`): read-only, hand-built SVG
  on the stage layout (no graph library); arrows are `aria-hidden`, so card text and the details panel must carry the
  same facts. The critical path counts unfinished required tasks; traversals stay cycle-guarded.
- Analytics and report UI (ADR-0045): `tz` (validated IANA name, bound parameter) only cuts days for
  `group_by=day`. Charts plot one measure on one axis, never a cost summed across bases, and always have a table
  view. The report's timeline drops people's free text (event `reason`).
- Artifacts (ADR-0047, `app/artifacts/`): bytes only in `LocalArtifactStorage` under `ARTIFACT_STORAGE_ROOT` (outside
  the repo), metadata in `artifacts` (RLS). Keys are `<workspace_id>/<artifact_id>` from UUIDs, never from names; file
  names go through `safe_filename`, media types through `media_type_for`. Only platform code creates artifacts
  (`records.store_artifact`; no upload API). Downloads verify size and SHA-256 first; a trigger allows only
  AVAILABLE -> DELETED. Never return storage keys or paths to clients.
- Software projects (ADR-0048): `workflows.kind = SOFTWARE_PROJECT` plans with schema version 2 (`project` + per-task
  `file_scope`/`effort_points`) on a curated profile from `app/projects/profiles.py` (immutable; new deps = new id).
  Scope matching lives only in `app/projects/scope.py` (case-insensitive; folders end in `/`); scopes of different
  tasks never overlap and every profile shared file has exactly one owner. General workflows keep version 1 plans.
- Project workspaces (ADR-0049, `app/projects/`): only `apply_job` writes them, after a task is COMPLETED
  (`project.apply`), via `ProjectWorkspaces.apply` (scope sync, path re-checks, no links, hardened git: no global
  config, empty hooks, `add --force`). One `workspace_applications` row per attempt (append-only). Contract v4 carries
  `file_scope`, `stack_profile` and capped `dependency_files`; `deterministic/v2` refuses files outside the scope.
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
