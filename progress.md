# AgentFlow Progress

## Current position
- Phase: 3 — Specialized agents (Phase 2 complete; Phase 1 released as `v0.1.0`, pushed with its tag)
- Last completed step: 3.6 — Rule-based agent selection with persisted rationale
- Next step: 3.7 — Agent proposal workflow with admin approval before activation, which needs a short plan and the owner's go-ahead

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
| 2.3 | `feat(planning): add versioned plan drafts with task decomposition` | `f1d8476` (pushed, CI ✔) |
| 2.4 | `feat(planning): add plan DAG validation and draft preview` | `8389c82` (pushed, CI ✔) |
| 2.5 | `feat(planning): confirm plan drafts into workflow tasks` | `83b7ccc` (pushed, CI ✔) |
| 3.1 | `feat(agents): add task contract, BaseAgent and structured submissions` | `61ac0ef` (pushed, CI ✔) |
| 3.2 | `feat(tools): add tool registry and server-side tool gateway` | `54bf1d4` (pushed, CI ✔) |
| 3.3 | `feat(llm): add LiteLLM provider adapter with usage capture` | `3abd85a` (pushed, CI ✔) |
| 3.4 | `feat(agents): add Research, Analyst, Writer and General agents` | `d51668f` (pushed, CI ✔) |
| 3.5 | `feat(agents): add Developer and QA agents without execution` | `46cb587` (pushed, CI ✔) |
| 3.6 | `feat(agents): add rule-based agent selection with stored rationale` | latest commit touching `docs/ADR/0023-rule-based-agent-selection.md` |

## Environment snapshot (2026-11-14)
| Item | Value |
|---|---|
| Repo | `C:\Users\shivramm\project\AgentFlow`, branch `main`; pushed to `origin` up to `46cb587` plus tag `v0.1.0` (owner-authorized) |
| Python / uv / Node / npm | 3.12.14 via uv 0.12.9 / Node 24.13.0 / npm 11.6.2 |
| Celery / kombu | 5.7.0 / 5.7.0 |
| Docker | 29.9.3 — `agentflow` stack healthy (Docker Desktop must be started first; it had stopped and was restarted in Steps 3.1 and 3.4) |
| Dev database | `agentflow` at `0013 (head)`; built-ins seeded; `agentflow_api` and `agentflow_worker` provisioned |
| Local `.env` | holds generated `WORKER_DB_PASSWORD` and (new) `API_DB_PASSWORD` (gitignored) |
| Port 8000 | Used by another local project (`easa`); smoke tests used 8010 |
| E2E | API :8011 (as `agentflow_api`), frontend :3100, database `agentflow_e2e` (reset each run), Redis DBs 2/3, installed Google Chrome |
| Frontend audit | `npm audit --omit=dev`: 0 vulnerabilities; dev-only: 8 moderate, all the existing `minimatch` chain via ESLint |
| Test HTTP client | `httpx2` 2.16.0 (replaces `httpx`, OD-10); `httpx` is back only as a LiteLLM dependency |
| LiteLLM | 1.109.1, imported by workers only when `MODEL_PROVIDER=litellm`; `.env` keeps the mock |

## Step 3.6 — rule-based agent selection (ADR-0023)
- **Owner decisions (2026-11-14):**
  - the platform suggests and people assign;
  - no history scoring until verification;
  - a new append-only table;
  - the Assign dialog ranks agents with reasons.
- **`app/agents/selection.py` (`selection/v1`, pure):**
  - hard filters: skills and runtime;
  - scored: `skill_fit` (40, specialists first), `tool_fit` (20, usable grants), `workload` (30 / (1 + in-flight));
  - not scored: `history`, `cost`, `latency` and `policy`, each with its reason;
  - order: eligible by score, then name, then id; excluded last.
  - Runnable references come from the instruction registry, so the API never loads the model gateway.
- **Service and API:**
  - `selection.rank_for_task` / `record_selection`;
  - `assign_agent` stores a server-computed `agent_selections` row and adds `selection_id`/`selection_rank` to the event;
  - `GET …/agent-suggestions` and `GET …/agent-selections` (Viewer).
- **Migration `0013_agent_selections`:** composite foreign keys, a `RESTRICT` user reference, rank/eligibility checks, an append-only trigger, and RLS tenant and platform policies (no worker grant).
- **Frontend:** the Assign dialog now uses the suggestions: suitable agents best first with a "Suggested" badge, score and reasons, plus a separate "Not suitable" list with the failed rules.

## Checks last executed (Step 3.6)
- **Full gate (`check.ps1 -E2E`) ✔ on the second run, exit 0:**
  - backend: ruff format (154 files), ruff check, mypy (154 files), alembic check;
  - pytest **938 passed** (929 plus 9 new: 6 unit ranking tests, 3 API tests for suggestions, the stored rationale with the append-only check, and workspace scoping);
  - frontend: eslint, typecheck, Vitest **98** (2 new: dialog ranking and free choice), Playwright **14 passed**, build.
- **First gate run failed in E2E.** Journey 8/9 hit a revision conflict on "Submit plan for approval":
  - **Cause:** the API log showed the task was created and the workflow refetched, but the page re-rendered late under load, so the click sent the old revision. The server refused it correctly.
  - **Fix:** the journey's inline task-adding steps (since Step 3.1) now wait for the new task's heading, as the shared `addTask` helper does. No assertion was removed.
- **Deliberate-bug check** (reverted): inverting the ranking order makes 2 ranking tests fail.
- **Environment:** the dev DB is at `0013`, and both roles were re-provisioned; no servers were left running after E2E.
- **Also fixed:** a SQLAlchemy 2.1 deprecation (`Result.tuples()`) in the new workload query.

## Step 3.5 — Developer and QA agents (ADR-0022)
- **Owner decisions (2026-11-14):**
  - the Developer delivers structured, path-checked file proposals;
  - QA designs and reviews tests only.
- **Runtimes:**
  - `DeveloperAgent` (`builtin://developer/v1`, `agent_development`) and `QAAgent` (`builtin://qa/v1`, `agent_qa`);
  - no shell, process, file or network access, only the model gateway;
  - the instructions forbid claiming execution, passing tests or written files.
- **Generic mechanisms on `ModelAgent`:**
  - `outputs_model`, sent as a second schema and checked, with `OUTPUTS_INVALID` / `TEST_CASE_UNKNOWN_CRITERION` triggering the corrective retry;
  - `platform_note` plus `note_unless_tools`; Research now uses the same mechanism.
- **`app/agents/outputs.py`:**
  - `ProposedFile` with path checks: relative POSIX, no traversal or absolute paths, drives, backslashes, `:` or spaces, at most 12 segments, no VCS/credential folders, no `.env*` except `.env.example`;
  - `DeveloperOutputs` and `QAOutputs` (test cases covering contract criteria).
- **Frontend:** the executions panel shows proposed files and tests (collapsible, `<pre>`) and designed test cases, as plain text, marked not applied or run.
- **Tests changed:** two Step 3.4 tests that listed Developer and QA as having no runtime now expect their runtimes. Unknown and custom references still fail.

## Checks (Step 3.5)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0:**
  - backend: ruff format (149 files), ruff check, mypy (149 files), alembic check;
  - pytest **929 passed** (900 plus 29 new: path, output and runtime tests for Developer and QA);
  - frontend: eslint, typecheck, Vitest **96** (2 new: proposed files and test cases rendered as plain text), Playwright **14 passed**, build.
- **Warnings.** pytest's 12 warnings are the known LiteLLM-internal pydantic notices.
- **Deliberate-bug check** (reverted): allowing `..` path segments makes 3 tests fail (2 path tests, 1 corrective-retry test).
- **Environment:** the dev DB stays at `0012` (no migration); no live model call was made; no servers were left running.

## Step 3.4 — built-in agent runtimes (ADR-0021)
- **Owner decisions (2026-11-14):**
  - versions without a runtime fail the attempt (`AGENT_RUNTIME_UNAVAILABLE`);
  - one corrective retry on invalid output;
  - Research works from model knowledge only.
- **`app/agents/instructions.py`:** immutable texts for `builtin://researcher|analyst|writer|general/v1`, plus common rules (contract is delimited data, never invent sources or results, one claim per criterion, verification is independent).
- **`app/agents/specialists.py`:**
  - `ModelAgent` makes one structured gateway call per try (version profile, temperature, output tokens capped by the token budget);
  - one corrective retry naming only issue codes;
  - `ResearchAgent` always appends a platform note about missing retrieval tools;
  - `RUNTIMES` / `runtime_for`;
  - deterministic mock defaults per purpose (`agent_research`, `agent_analysis`, `agent_writing`, `agent_general`).
- **Prompt:** instruction, rules, criterion ids and schema in the system message; the contract JSON inside `<task_contract>` (with `</` escaped and no platform IDs).
- **Runner and worker:**
  - `run_agent` chooses the runtime from the version's instruction reference;
  - `AgentContext.models` is the gateway;
  - the worker maps `AgentRuntimeUnavailableError` and `ModelGatewayError` to failed attempts with fixed messages.
  - `InvalidSubmissionError` moved to `app/agents/contract.py`.
- **E2E:** the stack forces `MODEL_PROVIDER=mock` whatever `.env` says. Journey 8/9 now runs the Writer runtime and checks the mock model's limitation text.

## Checks (Step 3.4)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0:**
  - backend: ruff format (147 files), ruff check, mypy (147 files), alembic check;
  - pytest **900 passed** (884 plus 16 new: 14 unit runtime tests, 2 integration tests: built-in runtime with `llm_usage` and submission, and a version without a runtime);
  - the real Celery-worker test now runs the built-in Writer runtime and checks its `llm_usage` row;
  - frontend: eslint, typecheck, Vitest **94**, Playwright **14 passed** (journey 8/9 runs the Writer runtime on the mock model), build.
- **Warnings.** pytest's 12 warnings are the known LiteLLM-internal pydantic notices (Step 3.3).
- **Deliberate-bug check** (reverted): removing the `</` escaping of the delimited contract makes the delimiter test fail.
- **Environment:**
  - Docker Desktop had stopped again; it was restarted and the stack came back healthy;
  - the dev DB stays at `0012` (no migration);
  - no live model call was made.

## Step 3.3 — LiteLLM provider adapter (ADR-0020)
- **Owner decisions (2026-11-13):**
  - any LiteLLM provider, configured through settings;
  - offline verification only;
  - one model for every profile.
- **New dependency:** `litellm` 1.109.1 (handoff §3), locked in `uv.lock`. It brings the `openai` SDK, `tiktoken`, `tokenizers`, `huggingface-hub`, `boto3`, `aiohttp` and `httpx`.
- **Settings:**
  - `MODEL_PROVIDER` is `mock` (default) or `litellm`;
  - `MODEL_API_KEY` (secret, redacted, rejected as a placeholder in production) is required unless `MODEL_API_BASE` is set;
  - `MODEL_TIMEOUT_SECONDS` defaults to 60.
  - `.env.example` documents an Anthropic example.
- **`app/llm/litellm_adapter.py`:**
  - offline mode set before import (no price-map, tokenizer or other downloads);
  - callbacks and message logging off, with LiteLLM's loggers routed through ours at WARNING;
  - the key passed per call; retries off; `drop_params`; a non-strict JSON schema when the model supports it;
  - the provider recorded as the routed one (e.g. `anthropic`).
- **Usage capture:**
  - the provider's input, output and cached tokens;
  - the other usage fields plus `reported_via: "litellm"`;
  - the request ID.
  - LiteLLM's `response_cost` is never read.
- **Findings:**
  - `claude-sonnet-5-5` accepts only `temperature=1` in LiteLLM, hence `drop_params`;
  - for Ollama, LiteLLM silently estimates missing token counts (new OD-21);
  - pydantic warns about LiteLLM's internal types, so that one message is filtered.

## Checks (Step 3.3)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0:**
  - backend: ruff format (144 files), ruff check, mypy (144 files), alembic check;
  - pytest **884 passed** (870 plus 14 new: 13 unit adapter/settings/hardening tests, 1 integration test writing an `llm_usage` row as the worker role, all offline);
  - frontend: eslint, typecheck, Vitest **94**, Playwright **14 passed**, build.
- **Warnings.** pytest reports 12 warnings, all pydantic `ReadOnly` notices about LiteLLM's internal types. pytest resets warning filters per test; a plain process confirmed the adapter's filter silences them in workers. The old `httpx` deprecation warning did not return.
- **Deliberate-bug check** (reverted): turning LiteLLM retries back on (`num_retries=2`) makes the argument test fail. LiteLLM's offline mock does not retry, so the failure test cannot detect retries by behaviour.
- **Environment:**
  - the local `.env` is unchanged, so it still uses the mock;
  - the dev DB stays at `0012` (no migration);
  - no live provider call was made, by owner decision.

## Step 3.2 — tool registry and gateway (ADR-0019)
- **Owner decisions (2026-11-13):**
  - no tool gets a working implementation yet;
  - a call outside the contract fails the attempt;
  - tool calls go in the API only, with no UI yet.
- **`app/tools/registry.py`:**
  - a `ToolDefinition` for every catalog tool (required access, optional implementation with strict schemas);
  - none implemented, so none available;
  - `usable_grants`.
- **`app/tools/gateway.py`:** `ToolGateway.invoke` checks:
  - the call limit (50 per attempt);
  - the contract grant and its access;
  - arguments, the handler, and the result and its size.
  - Fixed-message `ToolError`s; violations (permission, limit) are remembered.
- **Contract:** `allowed_tools` is a list of `ToolGrant {tool, access}` holding only usable grants.
- **Runner and worker:**
  - `AgentContext.tools` is the gateway;
  - `run_agent` raises the first violation even if the agent swallowed it;
  - the worker maps `ToolError` to a failed attempt with its code.
- **Migration `0012_tool_invocations`:**
  - one row per call (argument hash only), written in its own transaction;
  - RLS tenant and platform policies; worker SELECT and INSERT.
- **API:** `GET …/executions` includes each attempt's `tool_invocations`. Frontend types only, with no UI.

## Checks (Step 3.2)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0:**
  - backend: ruff format (142 files), ruff check, mypy (142 files), alembic check;
  - pytest **870 passed** (850 plus 20 new: 18 unit registry/gateway/runner, 1 worker-role integration test recording calls and failing the attempt, 1 API test of `tool_invocations`);
  - frontend: eslint, typecheck, Vitest **94**, Playwright **14 passed**, build.
- **Deliberate-bug check** (reverted): making the gateway forget violations makes 9 tests fail.
- **Environment:** the dev DB is at `0012`, and both roles were re-provisioned; no servers were left running after E2E.

## Step 3.1 — agent contract and submissions (ADR-0018)
- **Owner decisions (2026-11-13):**
  - each attempt's submission is stored in a column on `agent_executions`;
  - `BaseAgent.execute` is synchronous;
  - tasks get a `deliverables` column, filled from confirmed drafts;
  - the executions panel shows submissions.
- **`app/agents/contract.py`:**
  - `TaskContract` (handoff §5.3);
  - `AgentSubmission` with per-criterion `CriterionClaim`s;
  - `validate_submission`, which returns safe issue codes.
- **`app/agents/base.py`:** `BaseAgent` (`execute`, `get_capabilities`, and a `@final` `validate_output`) and `AgentContext`.
- **`app/agents/mock.py`:** `MockAgent` replaces `run_mock_agent`.
- **Runner:**
  - `claim` snapshots the contract data;
  - `build_contract` grants only enabled tools and caps the timeout at 600 s;
  - `run_agent` validates the output;
  - `finish(session, claim, outcome)` stores the submission.
  - The worker maps invalid output to `INVALID_SUBMISSION`.
- **Migration `0011_agent_contract`:**
  - `workflow_tasks.deliverables` (copied on plan confirmation; settable on manual tasks);
  - `agent_executions.submission`, allowed only on SUCCEEDED attempts.
- **Frontend:**
  - the executions panel shows the agent's claims as "not verified yet", plus open questions, assumptions and limitations;
  - the task form has a Deliverables field, and task details list deliverables.

## Checks (Step 3.1)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0:**
  - backend: ruff format (137 files), ruff check, mypy (137 files), alembic check;
  - pytest **850 passed** (829 plus 21 new: 17 unit contract/submission/mock agent, 3 runner integration, 1 task deliverables API);
  - frontend: eslint, typecheck, Vitest **94** (2 new: submission display), Playwright **14 passed** (journey 8/9 now adds a deliverable and a criterion, and sees the agent's unverified claim and the mock agent's limitation), build.
- **Deliberate-bug check** (reverted): dropping the missing-claim check makes 3 tests fail.
- **Environment:**
  - Docker Desktop had stopped; it was restarted and the stack came back healthy;
  - the dev DB is at `0011`, and both roles were re-provisioned;
  - no servers were left running after E2E.
- **Mistake, corrected:** `npx prettier` (not a project dependency) was run once by mistake. It reformatted four frontend files, which were restored and re-edited; `package.json` and the lockfile were untouched.

## Step 2.5 — plan confirmation (ADR-0017)
- **Owner decisions (2026-11-13):**
  - when tasks exist, the user chooses whether the draft replaces them or is added;
  - confirmation creates tasks only, and the existing Submit / Mark ready and Start actions come next;
  - warnings need explicit acknowledgement, and errors always block.
- **Migration `0010_plan_confirmation`:** adds `workflows.confirmed_plan_version` and the `PLAN_CONFIRMED` event type.
- **Service `tasks.confirm_plan_draft`:**
  - runs under the workflow lock and revision, and re-validates the draft against the active agents;
  - refuses with `PLAN_DRAFT_INVALID`, `PLAN_WARNINGS_NOT_ACKNOWLEDGED` or `EXISTING_TASKS_MODE_REQUIRED` (all 409);
  - reopens PLANNING from AWAITING_APPROVAL/READY;
  - on replace, removes active tasks as history (shared `_remove_from_plan` with manual removal);
  - creates PENDING tasks and dependencies with their events, then writes `PLAN_CONFIRMED`.
- **API:** `POST …/plan-drafts/{version}/confirm` (Member).
- **Frontend:**
  - "Confirm this draft" in the Plan draft tab (members, editable plans, drafts without errors);
  - `confirm-plan-dialog.tsx` with the Replace/Add choice and the warning acknowledgement;
  - a "Confirmed" badge; success switches to the Tasks tab.

## Checks (Step 2.5)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0:**
  - backend: ruff format (132 files), ruff check, mypy (132 files), alembic check;
  - pytest **829 passed** (824 plus 5 new confirmation integration tests);
  - frontend: eslint, typecheck, Vitest **92** (3 new), Playwright **14 passed** (journey 7 now confirms the draft and sees its task on the Tasks tab), build.
- **Deliberate-bug check** (reverted): skipping the warning-acknowledgement check makes `test_warnings_must_be_acknowledged` fail.
- **Environment:** the dev DB is at `0010` and both roles were re-provisioned; no servers were left running after E2E.

## Step 2.4 — DAG validation and plan preview (ADR-0016)
- **Owner decisions (2026-11-13):** a cyclic model reply is invalid output and is retried; skills no active agent provides are warnings; a separate "Plan draft" tab; any version can be previewed, newest first.
- **`app/planning/dag.py`** (pure functions):
  - `find_cycle` (Kahn) and `stages` (longest-path levels);
  - `analyse_plan`: `DEPENDENCY_CYCLE` (error); `REQUIRED_DEPENDS_ON_OPTIONAL`, `SKILL_NOT_AVAILABLE` and `STAGE_EXCEEDS_PARALLEL_LIMIT` (warnings).
- **`app/planning/schema.py`:** `PlanProposal`/`PlannedTask` moved here (no gateway imports) and the schema now rejects cycles. `plan.py` re-exports the names.
- **API:** `GET …/plan-drafts/{version}` (Viewer) returns the full draft, stages and issues. Skill coverage is computed against the ACTIVE agents' active versions at preview time (`agents_repository.active_capabilities`). An unknown version gets 404 `PLAN_DRAFT_NOT_FOUND`; a version below 1 gets 422.
- **Frontend:**
  - `plan-draft-preview.tsx`: a "Plan draft" tab with a version selector, errors and warnings naming tasks, stage columns of expandable task cards, and risks;
  - "Review draft" in the Requirements status card opens the tab.

## Checks (Step 2.4)
- **Backend gate (`check.ps1 -E2E`, backend part) ✔:**
  - ruff; mypy (131 files); alembic check;
  - pytest **824 passed** (814 plus 10 new: 7 unit DAG/schema, 3 preview integration).
- **Frontend, rerun after two test fixes ✔:**
  - eslint, typecheck;
  - Vitest **89** (4 new);
  - Playwright **14 passed**: journey 7 now opens the preview and checks Stage 1 shows "No problems found.";
  - build.
- **The two test fixes:**
  - A new component test's waits now allow 5 s, because the preview makes two requests in sequence; it timed out under gate load. The assertions are unchanged.
  - An E2E locator now uses an exact match: it also matched the collapsed task details.
- **Deliberate-bug check** (reverted): removing the schema's cycle rejection makes 2 tests fail.
- **Environment:** Docker Desktop's engine failed to start mid-step; the owner restarted Docker. The dev DB stays at `0009`; there is no migration in this step.

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
- **OD-21:** token counts LiteLLM estimates for providers that don't report usage (e.g. Ollama) look like reported ones. Phase 7 pricing must treat them as unverified.
- **Live provider path unverified:** Step 3.3 was verified offline only, by owner decision.
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
1. **Push Step 3.6** on the owner's go-ahead, and confirm CI on GitHub.
2. **Step 3.7 (agent proposal workflow with admin approval before activation):** present a short plan and get the owner's go-ahead.
3. **Before any hosted deployment:** settle OD-14, OD-19 and OD-20.
