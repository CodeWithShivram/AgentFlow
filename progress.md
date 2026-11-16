# AgentFlow Progress

## Current position
- Phase: 6 — Human intervention, in progress (Phases 2–5 complete; Phase 1 released as `v0.1.0`, pushed with its tag)
- Last completed step: 6.1 — Clarification and approval request model/API
- Next step: 6.2 — Workflow pause/resume, which needs a short plan and the owner's go-ahead

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
| 3.6 | `feat(agents): add rule-based agent selection with stored rationale` | `3fbde4f` (pushed, CI ✔) |
| 3.7 | `feat(agents): file platform agent proposals with permission review` | `437faab` (pushed, CI ✔) |
| 4.1 | `feat(orchestration): add LangGraph workflow assessment graph` | `de4da58` (pushed, CI ✔) |
| 4.2 | `feat(orchestration): persist graph checkpoints in PostgreSQL` | `c7657ef` (pushed, CI ✔) |
| 4.3 | `feat(execution): surface and retry failed jobs and report worker health` | `c23c846` (pushed, CI ✔) |
| 4.4 | `feat(scheduling): dispatch by priority within slot limits and allow pre-assignment` | `77b61c4` (pushed, CI ✔) |
| 4.5 | `feat(execution): stop cancelled agents and recover work stranded by lost workers` | `a737bec` (pushed, CI ✔) |
| 4.6 | `test(execution): cover parallel and sequential execution across whole graphs` | `cd50ba2` (pushed, CI ✔) |
| 5.1 | `feat(verification): verify submissions deterministically and accept passing tasks` | `fdda86b` (pushed, CI ✔) |
| 5.2 | `feat(verification): add semantic evaluation with structured findings` | `7f7805a` (pushed, CI ✔) |
| 5.3 | `feat(verification): retry failed attempts with corrective instructions and reassign once` | `7ec5310` (pushed, CI ✔) |
| 5.4 | `feat(planning): replan the remaining work of paused or blocked workflows` | `0012bb7` (pushed, CI ✔) |
| 5.5 | `feat(verification): escalate exhausted work and let only platform verification accept tasks` | `f51f77f` (pushed, CI ✔) |
| 6.1 | `feat(interventions): add clarification and approval requests` | latest commit touching `docs/ADR/0035-human-interventions.md` |

## Environment snapshot (2026-11-14)
| Item | Value |
|---|---|
| Repo | `C:\Users\shivramm\project\AgentFlow`, branch `main`; pushed to `origin` up to `f51f77f` plus tag `v0.1.0` (owner-authorized) |
| Python / uv / Node / npm | 3.12.14 via uv 0.12.9 / Node 24.13.0 / npm 11.6.2 |
| Celery / kombu | 5.7.0 / 5.7.0 |
| Docker | 29.9.3 — `agentflow` stack healthy (Docker Desktop must be started first; it had stopped and was restarted in Steps 3.1 and 3.4) |
| Dev database | `agentflow` at `0024 (head)`; built-ins seeded; `agentflow_api` and `agentflow_worker` provisioned |
| Local `.env` | holds generated `WORKER_DB_PASSWORD` and (new) `API_DB_PASSWORD` (gitignored) |
| Port 8000 | Used by another local project (`easa`); smoke tests used 8010 |
| E2E | API :8011 (as `agentflow_api`), frontend :3100, database `agentflow_e2e` (reset each run), Redis DBs 2/3, installed Google Chrome |
| Frontend audit | `npm audit --omit=dev`: 0 vulnerabilities; dev-only: 8 moderate, all the existing `minimatch` chain via ESLint |
| Test HTTP client | `httpx2` 2.16.0 (replaces `httpx`, OD-10); `httpx` is back only as a LiteLLM dependency |
| LiteLLM | 1.109.1, imported by workers only when `MODEL_PROVIDER=litellm`; `.env` keeps the mock |
| LangGraph | 1.2.22 (with langchain-core 1.8.0 and langsmith 0.14.17), imported by workers only; LangSmith tracing disabled; checkpoints via `langgraph-checkpoint-postgres` 3.2.1 |

## Step 6.1 — requests for people (ADR-0035)
- **Owner decisions (2026-11-16):**
  - all four sources (analysis questions, plan approval, agents' approval requests, escalations);
  - answers feed the next plan or replan draft, and nothing pauses;
  - members answer, admins approve or reject, responders recorded;
  - a Requests inbox plus "Needs your input" on the workflow page;
  - an agent's request is followed by a new attempt either way, and it counts.
- **Model:** `human_interventions` (migration `0024`) with:
  - RLS tenant and platform policies;
  - worker grants SELECT/INSERT/UPDATE;
  - a unique `(workflow_id, source_key)`;
  - the `INTERVENTION_OPENED`/`INTERVENTION_RESOLVED` events (identifiers only).
- **Engine** (`open_intervention`, `resolve_intervention`, `close_interventions`, and hooks in both transition functions):
  - entering AWAITING_APPROVAL opens a plan approval; leaving withdraws it unless decided;
  - reaching ESCALATED opens MANUAL_RECOVERY, which acting on the task resolves (attributed: assign, cancel and removal now pass the user id);
  - leaving WAITING_APPROVAL withdraws an undecided agent request;
  - a terminal workflow withdraws everything still open.
- **Sources:**
  - `analysis.finish` opens one CLARIFICATION per ambiguity and withdraws the previous analysis's open questions;
  - the plan job passes the current analysis's answers in a delimited `<clarifications>` section (`plan_generation/v2`, `replan/v2`);
  - `AgentSubmission.approval_request` makes the runner park the task in WAITING_APPROVAL and open an APPROVAL request.
- **Decisions** (`app/services/interventions.py`, router `/workspaces/{id}/interventions`):
  - list, get;
  - answer (MEMBER);
  - approve/reject (ADMIN);
  - plan approval moves the workflow to READY or PLANNING;
  - an agent's request starts the next attempt by the same agent (WAITING_APPROVAL → ASSIGNED, new edge), with `approved_actions` (contract v3) or `rework.REJECTED_INSTRUCTION`;
  - after the last allowed attempt it escalates instead (WAITING_APPROVAL → ESCALATED, SYSTEM, new edge).
  - The workflow's own approve and send-back buttons resolve the plan request with the same, attributed decision.
- **Supporting changes:**
  - `MAX_ATTEMPTS_PER_TASK` moved to `state_machine.py`, so the API applies the limit without importing worker code; the runner re-exports it;
  - `close_interventions` checks each row's loaded status, because sessions do not autoflush and a request closed earlier in the same transaction would otherwise be closed twice;
  - graph `orchestration/v5` gives WAITING_APPROVAL its own step, `approve_actions`.
- **Frontend:**
  - a "Requests" sidebar page (Open/Resolved/Withdrawn/All, paginated);
  - "Needs your input" on the workflow page;
  - members answer and admins approve or reject, with the agent's reason, risks, consequences and what each decision leads to, all as plain text;
  - the requirements panel points to them.
- **Tests:**
  - 10 integration tests (each source, decisions and roles, attribution, withdrawals, the attempt limit, workspace isolation, the worker's grants);
  - unit tests (the approval request schema, contract v3 note, rejection sentence, planner v2 delimiting, graph v5, the new edges);
  - 8 component tests (including "Needs your input" staying on screen while it refreshes);
  - E2E journey 13 (answer the analysis's question, find it on the Requests page) and Requests added to the navigation journey.
- **Tests changed (intended):**
  - version pins (planner v2, contract v3, graph v5);
  - the expected table lists (migrations, RLS);
  - the transition matrix;
  - the escalation tests now pick the escalation event, since an INTERVENTION_OPENED event follows it.
- **Found:** blank-only text passes `Annotated[SafeText, StringConstraints(min_length=1, strip_whitespace=True)]`, because the strip runs after the length check. Answers now have their own non-blank check. The same pattern on task titles is pre-existing and listed below.

## Checks last executed (Step 6.1)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0:**
  - backend: ruff format, ruff check, mypy (194 files), alembic check;
  - pytest **1114 passed**;
  - frontend: eslint, typecheck, Vitest **123**, Playwright **16 passed** (new journey 13), build.
- **Earlier gate runs failed twice, both fixed at the cause:**
  - the new answer component test typed key by key and passed 5 s; it now pastes the same text, and its assertions are unchanged;
  - journeys 7 and 12 could not click a task menu. "Needs your input" refetched under a new key on every poll and blinked out, shifting the page. It now keeps the previous result while refreshing, and a component test covers that (it fails without the fix).
- **A run overlapped with a second pytest session.** The two sessions share the test database roles, so that run errored. The affected tests passed when rerun alone. Never run two sessions at once.
- **Deliberate-bug checks** (each restored byte-for-byte):
  - removing `close_interventions`' loaded-status filter makes both plan-approval tests fail (an admin's approval is overwritten as WITHDRAWN in the same transaction);
  - removing `keepPreviousData` from "Needs your input" fails the new component test.
- **Environment:**
  - the dev DB is at `0024` and the roles are re-provisioned;
  - no servers were left running after E2E.

## Step 5.5 — escalation and platform-only acceptance (ADR-0034)
- **Owner decisions (2026-11-15):**
  - exhausted attempts escalate;
  - policy failures escalate;
  - pause at the deadline;
  - acceptance is enforced by the engine and the database.
- **Escalation:**
  - the third failure (runner, deterministic checks, semantic evaluation) and a lost worker's last attempt (recovery) now go to ESCALATED instead of FAILED;
  - `schedule_rework` escalates `NOT_RETRIED` failures (reason `POLICY_FAILURE`, with the codes);
  - attempts people add beyond three escalate again when they fail.
- **Bug fixed:** reassigning an ESCALATED task did not advance `attempt_count` (only REWORK_REQUIRED retries did). An escalated task whose attempt was not counted (evaluation unavailable, Step 5.2) would therefore have reused an attempt number at the next claim, breaking the unique constraint. `assign_agent` now takes the larger of the counted attempts (plus one for a rework retry) and `repository.max_attempt_number`. A new API test proves attempt 2 follows, and the existing retry test still holds.
- **Deadlines:** the recovery sweep pauses an overdue RUNNING workflow once (SYSTEM, `DEADLINE_PASSED`, `paused_at_deadline` in the report), and never again after people resume it.
- **Acceptance:**
  - `engine._require_platform_acceptance` refuses COMPLETED unless the latest successful attempt has PASSED `deterministic` and `semantic` results;
  - migration `0023` adds the trigger `workflow_tasks_platform_acceptance`, enforcing the same for every write;
  - the semantic job flushes its result before completing the task, which the new guard found missing.
- **Tests changed (intended behaviour changes):**
  - the exhausted-attempt tests (bounded attempts, lost last attempt, failing last attempt) expect ESCALATED and a BLOCKED workflow;
  - the policy-failure test expects ESCALATED;
  - the Step 4.4 test's engine stand-in now runs the real verification and evaluation;
  - the replan tests complete task A through a real attempt and verification instead of SQL;
  - the Step 1.7 lifecycle tests, which walk statuses by hand, now record the successful attempt and both verifiers' PASSED results before the final step, as the platform would store them.
- **Frontend:** none needed; escalated tasks already offer "Assign agent".

## Checks last executed (Step 5.5)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0:**
  - backend: ruff format, ruff check, mypy (188 files), alembic check;
  - pytest **1098 passed**: 1095 plus 3 new integration tests (the engine and trigger refusing an unverified completion, the deadline pause once, the escalated reassignment's attempt number), plus the updated escalation tests;
  - frontend: eslint, typecheck, Vitest **115**, Playwright **15 passed**, build.
- **An earlier gate run failed** on the Step 1.7 lifecycle tests, whose hand-walked completions the new guards rightly refused. Their setup now records the accepted attempt.

  A first version of the attempt-count fix also broke the existing retry test (a rework retry with no recorded attempt). The rule now takes the larger of the counted and recorded attempts.
- **Deliberate-bug check** (restored byte-for-byte): skipping the engine's acceptance check fails the acceptance test. The trigger then refuses at the database instead, which the test distinguishes.
- **Environment:**
  - Docker Desktop stopped once and was restarted;
  - the dev DB is at `0023`;
  - no servers were left running after E2E.

## Step 5.4 — replanning (ADR-0033; resolves OD-16)
- **Owner decisions (2026-11-15):**
  - paused or blocked workflows only, with nothing in flight; completed tasks kept, unfinished replaced;
  - the model drafts, people confirm;
  - the platform only suggests;
  - confirming follows the autonomy policy.
- **Request:** `POST …/replan` (Member; PAUSED/BLOCKED, analysis READY, no draft pending; else 409 `REPLAN_NOT_ALLOWED`).
- **The plan job** (`app/planning/plan.py`) drafts a replan when the workflow is PAUSED or BLOCKED:
  - `replan_basis` keys the completed tasks `done-N`, and the prompt `replan/v1` gets `<completed_tasks>` and `<unfinished_tasks>` as delimited data (titles, criteria, statuses);
  - planned tasks gain `depends_on_completed`, and unknown keys are retried as invalid output;
  - drafts store `kind` and `basis` (migration `0022`, with checks that a replan always has a basis);
  - the mock replan redoes each unfinished task on top of all completed ones.
- **Validation:** `analyse_plan(..., completed_keys=...)` reports `UNKNOWN_COMPLETED_TASK` (an error), at preview and at confirmation.
- **Confirmation** (`tasks._confirm_replan`) refuses:
  - workflows not PAUSED/BLOCKED (409);
  - non-admins on approval-required workflows (403);
  - in-flight tasks (409 `TASKS_IN_FLIGHT`);
  - stale bases (409 `PLAN_DRAFT_STALE`).

  It removes unfinished tasks as history (already CANCELLED ones are only marked removed), keeps COMPLETED and FAILED tasks, creates the new tasks with edges to completed ones, records `PLAN_CONFIRMED` (`kind`, created/removed/kept), and never resumes.
- **Orchestration v4:** a BLOCKED workflow gets `unblock` (the blocking required tasks, suggesting reassign, retry or replan); PAUSED mentions replanning. The generic `blocked` step now applies only to running workflows that cannot progress.
- **Frontend:**
  - "Replan remaining work" on paused or blocked workflows;
  - replan drafts badged, listing the kept completed work, with "Builds on completed" per task;
  - a replan confirm dialog (no replace/add choice), offered only to those allowed to confirm.
- **Found by the first gate run:** SQLAlchemy stores Python `None` in a JSONB column as JSON `null`, not SQL NULL, so every plain plan draft violated the new `replan_has_basis` check (15 plan-generation tests failed). `workflow_plans.basis` now uses `JSONB(none_as_null=True)`, as does `verification_results.findings` (Step 5.2) for consistency; this is Python-side only, with no migration.
- **Found by the second gate run:** the real-worker diamond test (Step 4.6) failed its overlap check. Since Step 5.2, its slow test adapter (1 s per call) also served the semantic evaluator, so evaluations held the worker's 2 threads. The evaluator now gets a fast mock gateway and the worker 3 threads; the overlap assertion is unchanged, and the test passed 3 runs in a row.
- **Found on the way:** removed tasks must be CANCELLED (a check constraint), so an optional FAILED task cannot be removed. Failed tasks now stay as final history while their work is replanned; the API test covers it.
- **Tests changed (intended behaviour changes):**
  - the graph version pins moved to v4;
  - the generic blocked-step test now assesses a RUNNING workflow, since BLOCKED workflows get `unblock`.

## Checks (Step 5.4)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0, on the third run:**
  - backend: ruff format, ruff check, mypy (187 files), alembic check;
  - pytest **1095 passed** (1084 plus 11 new):
    - 6 unit tests: schema, DAG checks against the basis, basis building, delimited prompt, mock replan, retries on unknown references;
    - 4 API tests: full replan of a blocked workflow (OD-16) with resume, refusals while running or in flight, admin confirmation for approval workflows, stale basis;
    - 1 graph test: `unblock`;
  - frontend: eslint, typecheck, Vitest **115** (3 new: replan preview, replan confirm, request and role), Playwright **15 passed**, build.
- **Earlier runs failed:**
  - the first, on the JSON-null bug;
  - the second, on the real-worker diamond's thread starvation.

  Both are fixed and described above.
- **Deliberate-bug check** (restored byte-for-byte): an empty in-flight set fails the in-flight refusal test.
- **Environment:** the dev DB is at `0022`; no servers were left running after E2E.

## Step 5.3 — rework, retries and reassignment (ADR-0032)
- **Owner decisions (2026-11-15):**
  - automatic bounded retries;
  - the platform reassigns once (amends ADR-0023);
  - everything is retried except policy failures;
  - instructions are platform text only.
- **Policy (`app/verification/rework.py`, `retry/v1`):** `schedule_rework` runs in the failure's transaction (runner, deterministic checks, semantic evaluation, recovery). It:
  - waits for people on `NOT_RETRIED` codes (TOOL_PERMISSION_DENIED, TOOL_CALL_LIMIT_EXCEEDED, AGENT_RUNTIME_UNAVAILABLE, OUTPUT_SCHEMA_INVALID, OUTPUT_SCHEMA_UNUSABLE);
  - reassigns once after two agent failures (not WORKER_LOST) by the current agent, when another eligible agent exists. It stores an `agent_selections` row with `decided_by_user_id` NULL and writes `TASK_REASSIGNED` (SCHEDULER);
  - otherwise retries the same version (`TASK_ASSIGNED`, `automatic_retry`, `policy`);
  - always increments `attempt_count`.
- **Instructions:**
  - `instructions_for` maps the previous attempt's error code and verification errors to fixed sentences (criteria by id, deliverables by position, max 20);
  - the runner computes them at claim and stores them in `agent_executions.rework_instructions` (migration `0021`);
  - they go on the contract (`contract_version` 2) and, for model agents, in a platform message after the delimited contract.
- **Worker grants:** `agent_selections` SELECT and INSERT (re-provisioned).
- **API and UI:** `rework_instructions` on each attempt; "Corrective instructions given" in the attempts panel.
- **Recovery:** a lost worker now goes through `schedule_rework` (same behaviour: same agent, never a reassignment).
- **Found on the way:** the selection rules count only agents with a runtime as eligible, so the reassignment tests give their agents the built-in General runtime reference. They use a skill pair no built-in agent has, so the outcome doesn't depend on test order.
- **Tests changed (intended behaviour changes):**
  - the bounded-attempts test no longer retries by hand;
  - the two verification-failure tests now expect the automatic retry (the VERIFICATION_FAILED event is second to last);
  - the verification test world can create several agents with given skills.
- **Follow-up:** the selection rules' history factor is still `not_scored`; scoring it from verification outcomes needs a new rules version.

## Checks (Step 5.3)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0:**
  - backend: ruff format, ruff check, mypy (185 files), alembic check;
  - pytest **1084 passed** (1074 plus 10 new):
    - 5 unit tests: instruction mapping, transient failures, cap, policy codes, the platform message after the contract;
    - 5 integration tests: retried with instructions, reassigned once, same agent when no other is eligible, and policy failures waiting for people (2 cases);
  - frontend: eslint, typecheck, Vitest **112** (1 new: corrective instructions), Playwright **15 passed**, build.
- **Deliberate-bug check** (restored byte-for-byte): `MAX_REASSIGNMENTS = 0` fails the reassignment test.
- **Environment:**
  - Docker Desktop had stopped and was restarted;
  - the dev DB is at `0021`, and roles were re-provisioned;
  - no servers were left running after E2E.

## Step 5.2 — semantic evaluation (ADR-0031)
- **Owner decisions (2026-11-15):**
  - every passing submission is evaluated;
  - not met or a conflict fails, partly met, incomplete and unstated assumptions are warnings;
  - retry, then escalate without counting against the agent.
- **Flow:**
  - passing deterministic checks now request `task.evaluate`, and the task stays VERIFYING;
  - `app/verification/semantic.py` (Celery `agentflow.evaluate_task`, 300/330 s limits) runs claim → up to 3 model calls (`semantic_evaluation` purpose, recorded in `llm_usage` against the attempt) → finish under the workflow lock: COMPLETED, REWORK_REQUIRED/FAILED, or ESCALATED (`EVALUATION_UNAVAILABLE`).
- **Evaluator `semantic/v1`:**
  - the strict `Evaluation` model: an objective verdict and reason; per-criterion verdict, evidence and reason; complete, consistent, assumptions_explicit and conflicts_with_requirements; a summary;
  - findings must cover each criterion once, or the reply is retried with issue codes;
  - contract and submission are delimited (every `</` escaped), with no platform ids;
  - a mock default finds everything met.
- **Decision (`decide`):**
  - errors: OBJECTIVE_NOT_MET, CRITERION_JUDGED_NOT_MET, CONFLICTS_WITH_REQUIREMENTS;
  - warnings: OBJECTIVE_PARTIAL, CRITERION_JUDGED_PARTIAL, INCOMPLETE, INCONSISTENT, ASSUMPTIONS_NOT_EXPLICIT.
- **Migration `0020`:** `verification_results.findings` (JSONB, NULL for checks); the findings are untrusted model text, shown as plain text only.
- **Recovery:** interrupted evaluations (claimed more than 450 s ago, still pending) are escalated with `EVALUATION_INTERRUPTED`; lost `task.evaluate` messages are resent (`reevaluated`, `interrupted_evaluations`).
- **State machine fix found by a test:** VERIFYING now lasts through a model call, but had no edge to CANCELLED. A person couldn't cancel such a task, and cancelling its workflow failed. VERIFYING → CANCELLED (people or system) was added, and the state machine test's VERIFYING exemption removed (stricter).
- **Frontend:** one section per verifier ("Platform verification: deterministic checks" / "…: semantic evaluation"); the evaluator's objective and per-criterion findings shown as plain text; issue labels for the new codes.
- **Tests changed (intended behaviour changes):**
  - the deterministic pass now leaves the task VERIFYING (the 5.1 pass test now runs the evaluation too);
  - the scenario helper also runs the evaluation;
  - real-worker tests patch the worker module's gateway, and the single-task one filters `llm_usage` by purpose;
  - the API test expects both results;
  - E2E journey 9 names both verification sections.

## Checks (Step 5.2)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0:**
  - backend: ruff format, ruff check, mypy (182 files), alembic check;
  - pytest **1074 passed** (1056 plus 18 new: 12 unit tests of the evaluator's rules, prompt, mock and bounded attempts; 6 integration tests of not met → rework, warnings passing, escalation, duplicate and stale requests, cancellation during evaluation, and recovery);
  - frontend: eslint, typecheck, Vitest **111** (1 new: evaluator findings), Playwright **15 passed**, build.
- **Deliberate-bug check** (restored byte-for-byte): making the evaluator's "criterion not met" only a warning fails one unit test and one integration test.
- **Environment:** the dev DB is at `0020`; no servers were left running after E2E.

## Step 5.1 — deterministic verification (ADR-0030)
- **Owner decisions (2026-11-15):**
  - failures go to rework and people retry; a failure on the third attempt fails the task;
  - NOT_MET fails, PARTIAL is a warning;
  - `jsonschema` is a direct dependency;
  - results show in the attempts panel.
- **Dependency:** `jsonschema` 4.26.0 is now direct. It was already locked through LiteLLM, so nothing new was downloaded; it is added to the existing mypy `ignore_missing_imports` override, as for Celery.
- **Flow:**
  - the engine writes `task.verify` whenever a task reaches SUBMITTED;
  - `app/verification/job.py` (Celery `agentflow.verify_task`, execution queue) runs as one transaction: inbox, workflow lock, still SUBMITTED, checks on the latest successful attempt, a stored result, then VERIFYING → COMPLETED / REWORK_REQUIRED / FAILED (VERIFIER).
- **Checks (`deterministic/v1`):** submission, criteria_coverage, criteria_claims, deliverables, output_schema (capped at 20 mismatches, remote `$ref` never fetched), runtime_outputs (Developer/QA), platform_note. Issues are codes, severities and task-data locations only.
- **Migration `0019`:** `verification_results` (append-only trigger, RLS tenant and platform policies, unique `execution_id` + `verifier`); worker grants SELECT and INSERT.
- **API:** `GET …/tasks/{task_id}/verification`, and `verifications` on each attempt in `…/executions`.
- **Recovery:** the sweep also resends `task.verify` messages published 15 minutes ago and never consumed (`reverified`).
- **Orchestration:** `orchestration/v3`; the awaiting-verification summary no longer says "arrives in Phase 5".
- **Frontend:**
  - a Platform verification section per attempt: Passed or Failed, the verifier, and each issue in plain words with its criterion, deliverable or schema location;
  - a polling fix: the workflow page used to stop polling before the post-completion reassessment arrived, leaving a stale Next-step banner. `isLive` now keeps polling for up to the five existing reassessment checks.
- **Tests changed (intended behaviour changes):**
  - the scenario tests' verification helper now runs the real verification job;
  - both real-worker tests now wait for COMPLETED, since the worker also verifies;
  - the RLS and fresh-schema table lists include `verification_results`;
  - the graph version pins moved to v3, and the awaiting-verification summary test asserts the new text exactly;
  - E2E journey 9 now ends Completed, with the banner gone and the verification shown; journey 12 now runs the dependent task too (its timeout is raised to 240 s, and it took 50 s).

## Checks (Step 5.1)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0:**
  - backend: ruff format, ruff check, mypy (179 files), alembic check;
  - pytest **1056 passed** (1026 plus 30 new: 21 unit tests of the checks, 9 integration tests of the job, recovery and grants; plus the API assertions added to the execution-history tests);
  - frontend: eslint, typecheck, Vitest **110** (3 new: two for the verification section, one for polling after completion), Playwright **15 passed**, build.
- **Deliberate-bug check** (restored byte-for-byte): downgrading the agent's NOT_MET claim to a warning fails one unit test and two integration tests (rework, and failing the last attempt).
- **Found on the way:**
  - the first E2E run of journey 12 failed on a stale Next-step banner. The worker log showed the post-completion assessment was stored after the page had stopped polling: a Step 4.1 polling gap, fixed by `isLive`;
  - a test variable named `secret` tripped the secret scan; it was renamed `agent_text`. That file's ruff, mypy and tests were rerun after the gate.
- **Environment:** the dev DB is at `0019`, and roles were re-provisioned; no servers were left running after E2E.

## Step 4.6 — parallel and sequential execution tests
- **Owner decisions (2026-11-15):**
  - also run a graph through a real two-thread Celery worker;
  - seeded random graphs with fixed seeds;
  - an E2E journey for the parallel half;
  - write `docs/TESTING.md`.
- **`backend/tests/integration/test_execution_scenarios.py`:** workflows with pre-assigned tasks, driven to completion with a verification stand-in:
  - a chain, a diamond, a fan-out wider than the limit by priority, a resume with ready and waiting tasks, and five simultaneous claims against a limit of 2;
  - 20 seeded random graphs with invariant checks at every claim;
  - a diamond through a real Celery worker (threads pool, concurrency 2, Redis DB 13), checking that B's and C's attempts overlap and that A and D are ordered.
- **Bug found and fixed (`app/workflows/engine.py`, ADR-0028 amended):** when several pre-assigned tasks became ready in one transaction, each was dispatched as it became ASSIGNED, so creation order beat priority and extra dispatches were written. The engine now:
  - assigns the newly ready tasks first, then decides once (`transition_task(..., dispatch=False)`);
  - reconciles before dispatching on resume;
  - counts tasks already dispatched in the transaction as taking a slot.
- **Test isolation fix:** a second real-worker test in one pytest process published through the first test's cached Celery broker pools (a different Redis database), so its worker never received the messages. Both real-worker tests now reset `celery_app._pool` and `celery_app.amqp._producer_pool` with `monkeypatch`, and pass in either order.
- **E2E journey 12:** three pre-assigned tasks; after the start, the two independent ones reach Submitted by themselves, and the dependent one stays Pending (the banner reads "2 tasks submitted and awaiting verification"). With the E2E stack's single solo worker, they run one after the other; the overlap is shown by the threaded real-worker test.
- **Docs:** `docs/TESTING.md` (suites, how to run them, rules for new tests, the execution scenario matrix, known Windows noise); linked from the README and CLAUDE.md.
- **No ADR of its own:** the step adds tests and fixes a bug against ADR-0028's decisions, so ADR-0028 is amended instead.

## Checks (Step 4.6)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0:**
  - backend: ruff format, ruff check, mypy (172 files), alembic check;
  - pytest **1026 passed** (1000 plus 26 new scenario tests: 6 named scenarios, 20 seeded random graphs and the real-worker diamond);
  - frontend: eslint, typecheck, Vitest **107**, Playwright **15 passed** (journey 12 added), build.
- **An earlier gate run failed** on the real-worker diamond, which timed out. That led to the test isolation fix above: the failure reproduced deterministically whenever two real-worker tests ran in one process, and both orders pass after the fix.
- **Deliberate-bug check** (restored byte-for-byte): putting back the per-task dispatch in `reconcile_workflow` (`dispatch=True`) fails the resume test.
  - The fan-out test passed under that mutation: with the bug, its outcome depends on the order of random task ids (tasks created in one transaction share a timestamp), so it catches the regression only sometimes. The resume test catches it every time.
- **Environment:**
  - Docker Desktop had stopped at the start of the session and was restarted;
  - the dev DB is still at `0018`; no servers were left running after E2E.

## Step 4.5 — cancellation and restart recovery (ADR-0029)
- **Owner decisions (2026-11-14):**
  - lost attempts retry automatically, bounded by the three attempts;
  - a time-based sweep detects lost workers;
  - cancelled agents stop at their next model or tool call;
  - the sweep also resends lost dispatches and fails interrupted planning jobs.
- **Cooperative cancellation (`app/execution/runner.py`):**
  - `AttemptGuard` runs `is_current` before every model call (`_GuardedModels`) and tool call (`ToolGateway(before_call=...)`); a stopped call writes no usage or invocation row;
  - `AttemptCancelledError` is raised even if the agent caught it, ahead of tool violations;
  - `attempt_is_current`: execution RUNNING and task IN_PROGRESS, so pausing keeps attempts running; database errors let the attempt go on;
  - `AgentContext.models` is now the `ModelCaller` protocol (`app/llm/gateway.py`).
- **Recovery sweep (`app/execution/recovery.py`, Beat `agentflow.recover` every 60 s, maintenance queue):**
  - lost attempts (RUNNING more than 660 s + 120 s): `WORKER_LOST`, then REWORK_REQUIRED → ASSIGNED (SCHEDULER, `automatic_retry`) with `attempt_count + 1`, or FAILED after the third attempt; a cancelled task's lost attempt becomes CANCELLED;
  - lost dispatches: the latest dispatch of an ASSIGNED task in a RUNNING workflow, published over 15 min ago and never claimed, is resent within free slots;
  - interrupted planning jobs: a PENDING analysis/plan request claimed over 450 s ago is failed through `analysis.record_failure` / `plan.record_failure` (`*_INTERRUPTED`).
- **Migration `0018`:** partial index `ix_agent_executions_running_started_at`. No new tables or grants.
- **Refactors:** the planning time limits moved to `analysis.JOB_*_TIME_LIMIT_SECONDS` and the hard agent limit to `runner.AGENT_HARD_TIME_LIMIT_SECONDS` (values unchanged).
- **Frontend:** no change; attempt error codes and event reasons already show as text.
- **Tests changed:** run_agent calls whose agents reach models or tools now pass `is_current` (unit: always current; integration: the real check against the test database), because the default check reads the worker database; the Celery route test includes `agentflow.recover`.

## Checks (Step 4.5)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0:**
  - backend: ruff format, ruff check, mypy (171 files), alembic check;
  - pytest **1000 passed** (988 plus 12 new):
    - 9 integration tests: a lost worker's task retried and completed; a lost last attempt failing the task; a cancelled task's lost attempt closed as cancelled; a running agent stopped at its next call after cancellation; pausing keeps an attempt current; a lost dispatch resent once; a claimed or deferred dispatch never resent; interrupted analysis and plan requests failed;
    - 2 unit tests: no model call after cancellation; the guard stays stopped when the agent swallows the error;
    - 1 unit test: recovery thresholds above the worker time limits;
  - frontend: eslint, typecheck, Vitest **107**, Playwright **14 passed**, build.
- **Deliberate-bug checks** (both restored byte-for-byte):
  - making `attempt_is_current` ignore the task status fails both cancellation tests;
  - dropping the retry's `attempt_count` increment fails the lost-worker test.
- **Environment:** the dev DB is at `0018`, and roles were re-provisioned; no servers were left running after E2E.

## Step 4.4 — dependency-aware scheduling (ADR-0028)
- **Owner decisions (2026-11-14):**
  - pre-assignment of PENDING tasks is allowed; they run when ready;
  - per-workflow limit only (`max_parallel_tasks`), no workspace-wide cap.
- **Engine (`app/workflows/engine.py`):**
  - `_dispatch_waiting` dispatches only as many ASSIGNED tasks as there are free slots, in `dispatch_order` (priority desc, then `created_at`, then id), once per task per transaction (`_transaction_marker`, shared with `request_orchestration`);
  - it runs after a task becomes ASSIGNED, after a task leaves a slot, and on resume; assigning no longer writes a dispatch directly;
  - `reconcile_workflow` moves a pre-assigned task READY → ASSIGNED as SCHEDULER (`pre_assigned: true`).
- **Service (`app/services/tasks.py`):** assigning a PENDING task pins the version and records `TASK_ASSIGNED`/`TASK_REASSIGNED` with `pre_assigned: true`, without a status change (refused in terminal workflows); changing `required_skills` clears the pin (`pre_assignment_cleared`).
- **Orchestration:** `orchestration/v2` reports ASSIGNED tasks as waiting for a free slot.
- **Frontend:** "Pre-assign agent…" in the menu of PENDING tasks in non-terminal workflows, opening the assignment dialog.
- **Tests changed (intended behaviour changes):**
  - the parallel-limit test no longer expects a dispatch for a task assigned while the slot is busy (it now writes one manually to show the claim still defers);
  - the API test that expected 409 when assigning a PENDING task now expects a pre-assignment (200, PENDING, no dispatch);
  - the graph version pins moved to v2, and a PLANNING PENDING task's menu now includes "preassign".

## Checks (Step 4.4)
- **Full gate (`check.ps1 -E2E`):**
  - backend ✔: ruff format (169 files), ruff check, mypy (169 files), alembic check;
  - pytest **988 passed** (982 plus 6 new: 2 unit graph cases for tasks waiting for a slot, 3 integration scheduling tests for concurrency in priority order, freed slots by priority and pre-assigned tasks dispatched when ready, and 1 API test for clearing a pre-assignment on a skills change);
  - frontend: lint ✔ and typecheck ✔. The gate's Vitest stage then failed on one 5-second timeout in an untouched file (`permission-review.test.tsx`) while the machine was busy (CPU about 80%: browser and editor). In the runs before it, other untouched UI tests had timed out the same way, and they passed when run alone (16/16);
  - so the remaining frontend stages were rerun in the gate's order with the gate's commands: Vitest **107 passed**, Playwright **14 passed**, build ✔. No test timeouts were changed.
- **Deliberate-bug check** (restored byte-for-byte): reversing `dispatch_order` (lowest priority first) makes both priority tests fail.
- **Environment:** the dev DB is still at `0017` (no migration in this step); no servers were left running after E2E.

## Step 4.3 — outbox hardening, failed jobs and worker health (ADR-0027)
- **Owner decisions (2026-11-14):**
  - failed jobs on the workflow page, with admin retry;
  - stranded executions (OD-17) moved to Step 4.5;
  - readiness reports the relay heartbeat and backlog.
- **Permanent failures:** `outbox.PermanentPublishError` for no route, or a payload missing the ids in `REQUIRED_PAYLOAD`; the relay marks such events FAILED after one attempt (`mark_failed(permanent=True)`).
- **Failed jobs (resolves OD-18):**
  - `app/services/background_jobs.py`;
  - `GET …/workflows/{id}/failed-jobs` (Viewer), `GET /workspaces/{id}/failed-jobs` (ADMIN, paginated), `POST …/failed-jobs/{event_id}/retry` (ADMIN, workflow lock);
  - a retry resets the event to PENDING with 0 attempts and writes `BACKGROUND_JOB_RETRIED` (migration `0017`). Responses expose `error_type` only, never `last_error`.
- **Heartbeat:**
  - the relay task writes `agentflow:workers:relay_heartbeat` (run time and oldest due-pending age, TTL 300 s) to the app Redis database after each run; failures are logged, never fatal;
  - `/health/ready` adds `workers`: ok / unavailable (silent > 60 s) / lagging (backlog > 300 s).
- **Frontend:** a "N background jobs failed" section on the workflow page (purpose, task, attempts, error type, time), with Retry for admins.
- **Tests changed:**
  - the readiness tests now write a relay heartbeat (the unit test expects `workers: unavailable` without Redis);
  - the unknown-route test now expects the more specific `PermanentPublishError`, and a missing-payload test was added.

## Checks (Step 4.3)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0:**
  - backend: ruff format (169 files), ruff check, mypy (169 files), alembic check;
  - pytest **982 passed** (965 plus 17 new: 9 unit heartbeat/error-type tests, 1 unit missing-payload test, 7 integration tests for failed-job visibility, retry and audit, workspace scoping, permanent publish failures and heartbeat recording, plus 2 readiness cases);
  - frontend: eslint, typecheck, Vitest **107** (3 new: failed-jobs section), Playwright **14 passed** (the stack's readiness wait now includes the relay heartbeat), build.
- **Deliberate-bug check** (reverted): making the relay retry permanent failures makes both permanent-failure tests fail (the events stay PENDING).
  - A first attempt at this check is void: Docker had stopped (machine restart), so both tests errored at setup with connection timeouts. Docker Desktop was restarted and the check rerun.
- **Environment:**
  - the dev DB is at `0017`; no servers were left running after E2E;
  - in this session the Git Bash shell lacked coreutils (`cp`, `grep`), so PowerShell was used for those steps.

## Step 4.2 — orchestration checkpoints (ADR-0026)
- **Owner decisions (2026-11-14):**
  - the library checkpointer on our own tables;
  - the latest 20 checkpoints per workflow.
- **New dependency:** `langgraph-checkpoint-postgres` 3.2.1 (plus `psycopg-pool` 3.3.3).
- **Migration `0016_orchestration_checkpoints`:**
  - the library's four tables with its columns and keys, our index names, and its 10 migrations recorded as applied (`setup()` is never called, and is a tested no-op);
  - RLS enabled and forced with only the platform policy.
  - Models mirror the tables, so `alembic check` covers them; a unit test pins the library's migration count and columns.
- **Access:** the API is excluded from all four tables. The worker gets SELECT/INSERT/UPDATE/DELETE on the data tables and nothing on `checkpoint_migrations`.
- **Job:**
  - a `PostgresSaver` on the job session's psycopg connection, so checkpoints commit with the assessment under the workflow lock;
  - thread = workflow id; `next_step.checkpoint_id` is recorded.
  - `assess()` returns `(step, checkpoint_id)`.
- **Retention (hourly cleanup):** `checkpoints` (beyond 20 per thread, or workflow gone), `checkpoint_writes` (checkpoint gone), `checkpoint_blobs` (no longer referenced).

## Checks (Step 4.2)
- **Full gate (`check.ps1 -E2E`) ✔ on the second run, exit 0:**
  - backend: ruff format (164 files), ruff check, mypy (164 files), alembic check;
  - pytest **965 passed** (961 plus 4 new: 2 unit pin tests for library migrations and columns, 2 integration tests for per-workflow checkpointing with no API access, no prompt text and a no-op `setup()`, and retention keeping the newest 20 and dropping orphans);
  - frontend: eslint, typecheck, Vitest **104**, Playwright **14 passed**, build.
- **First gate run failed:** `test_every_table_is_workspace_scoped_or_explicitly_exempt` correctly flagged the four checkpoint tables. They were added to its exemption list, each with its reason (library schema, worker-only, platform-only RLS, keyed by workflow id).
- **Deliberate-bug check** (reverted): making retention keep the oldest checkpoints makes the retention test fail.
- **Environment:** the dev DB is at `0016`, and both roles were re-provisioned; no servers were left running after E2E.

## Step 4.1 — LangGraph orchestration graph (ADR-0025)
- **Owner decisions (2026-11-14):**
  - assess and report only, with no new automatic work;
  - a workflow page banner.
- **New dependency:** `langgraph` 1.2.22 (handoff §3), locked in `uv.lock`. It brings `langchain-core`, `langgraph-checkpoint`/`-prebuilt`/`-sdk` and `langsmith`; LangSmith tracing is disabled via `langsmith.configure(enabled=False)`.
- **`app/orchestration/graph.py` (`orchestration/v1`):**
  - state: workflow status and task snapshot (identifiers and statuses only), counts, `next_step`;
  - nodes: `count_tasks`, a conditional `route`, and terminal nodes (`finished`, `workflow_step`, `assign_agents`, `running`, `awaiting_verification`, `blocked`) returning `{kind, actor, summary, task_ids}`.
- **Trigger and job:**
  - `engine.request_orchestration` writes one `workflow.orchestrate` outbox row per status-changing transaction (deduplicated by a per-transaction marker);
  - `app/orchestration/job.py` (Celery `agentflow.orchestrate_workflow`): inbox deduplication, workflow lock, snapshot, graph, then stores `workflows.next_step` without bumping `revision`.
- **Migration `0015_orchestration_next_step`:** `workflows.next_step` JSONB. `WorkflowResponse.next_step` is null until the first assessment.
- **Frontend:**
  - a "Next step" banner (title by actor, summary, assessed time);
  - after new events, up to five extra polls refresh the workflow while its assessment is older than the latest event.
- **Tests changed:** the relay and dispatch tests remove orchestration requests in their setup, as they did for analysis requests. The real-worker test selects the dispatch event by type.
- **Bug found by a new test and fixed:** tasks awaiting rework keep their previous assignment, and were first counted but left out of the listed task IDs. Counting and listing now share one rule.

## Checks (Step 4.1)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0:**
  - backend: ruff format (161 files), ruff check, mypy (161 files), alembic check;
  - pytest **961 passed** (941 plus 20 new: 17 unit graph tests including the LangSmith-off check, and 3 integration tests: one request per transaction, stored without a revision bump, redelivery, any delivery order, a foreign-workspace delivery);
  - frontend: eslint, typecheck, Vitest **104** (4 new: banner and stale-assessment check), Playwright **14 passed** (journey 8/9 now waits for the banner to say "awaiting verification"), build.
- **Warnings.** pytest's 12 warnings are the known LiteLLM-internal pydantic notices.
- **API imports:** the API process loads neither LangGraph, LangSmith, the graph nor LiteLLM (checked).
- **Deliberate-bug check** (reverted): removing the per-transaction deduplication makes the delivery-order test fail (more than one request per transaction).
- **Environment:** the dev DB is at `0015`, and both roles were re-provisioned; no servers were left running after E2E.

## Step 3.7 — platform agent proposals (ADR-0024)
- **Owner decisions (2026-11-14):**
  - proposals are filed automatically when a draft is confirmed;
  - a safe template, with no model;
  - a per-tool permission review.
- **The gap:** the built-ins cover every single skill, so the gap is a task whose skill **set** no single active agent covers (no eligible ADR-0023 candidate).
- **Trigger:**
  - `confirm_plan_draft` checks each distinct new skill set when the workflow allows proposals, and `agents.propose_specialists` files PENDING proposals;
  - it skips skill sets already pending and specialists that already exist by name;
  - audit with no actor and `source: platform`; `PLAN_CONFIRMED.agent_proposals_created`.
- **Template:** `Specialist: <skills>`, category `custom`, exactly the task's skills, `builtin://general/v1`, no tools, default model and limits, and a rationale naming the task. `requested_by` is NULL. Approval is unchanged (ADMIN, not the author; INACTIVE agent).
- **Migration `0014_platform_agent_proposals`:** `source_workflow_id`/`source_task_id` with composite workspace keys, a task-needs-workflow check, and the freeze trigger extended to the links (CREATE OR REPLACE; downgrade restores the 0003 version).
- **API and frontend:**
  - the proposal response has its source links, and the catalog tools have `usable_now`;
  - the Proposals page shows "by the platform" with a source-workflow link, and a **Permission review** (risk, usable now or permission only, owner needed for HIGH, or "Requests no tools").

## Checks (Step 3.7)
- **Full gate (`check.ps1 -E2E`) ✔, exit 0:**
  - backend: ruff format (155 files), ruff check, mypy (155 files), alembic check;
  - pytest **941 passed** (938 plus 3 new integration tests: one proposal per uncovered skill set with dedupe and INACTIVE approval; none without the setting; frozen source links). The catalog test now also checks `usable_now`;
  - frontend: eslint, typecheck, Vitest **100** (2 new: permission review), Playwright **14 passed**, build.
- **Migration round trip:** `0014` downgraded and re-applied cleanly on the dev DB before the gate.
- **Deliberate-bug check** (reverted): ignoring the workflow's "Allow agent proposals" setting makes the no-proposals test fail.
- **Environment:** the dev DB is at `0014`, and both roles were re-provisioned; no servers were left running after E2E.

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

## Checks (Step 3.6)
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
- **Blank titles (found in 6.1, pre-existing):** a task title of only spaces passes validation and is stored empty (`Title` in `app/schemas/workflows.py` strips after the length check). To fix with a non-blank check like the 6.1 answers.
- **Requests do not expire yet:** that is Step 6.3, and `expires_at` is reserved for it.
- **OD-20:** a non-superuser schema owner for migrations, before any hosted deployment.
- **OD-21:** token counts LiteLLM estimates for providers that don't report usage (e.g. Ollama) look like reported ones. Phase 7 pricing must treat them as unverified.
- **Live provider path unverified:** Step 3.3 was verified offline only, by owner decision.
- **OD-14:** trusted proxy IP handling, before hosted deployment.
- **OD-19:** the hosted frontend must share a host with the API (CSRF cookie readability); a CSP with nonces is not set yet.
- **Recovery latency** (ADR-0029): a crashed attempt keeps its slot for up to about 13 minutes; on the Windows solo pool, an attempt slower than that can be recovered while still running (its result is then discarded).
- **Readiness depends on workers** (ADR-0027): the API reports not ready without a running worker and Beat.
- **Celery:** time limits are not enforced by the Windows `solo` pool, only by Linux prefork. Exactly one Beat must run. Dispatch latency is up to 5 s.
- **Polling:** live updates poll, since there is no SSE endpoint. The dashboard makes 13 count requests, and Activity covers only the 10 newest workflows.
- **Still open:** the dev-only `minimatch` finding and the Redis licence review.

## Blockers
None.

## Next exact action
1. **Push Step 6.1** on the owner's go-ahead, and confirm CI on GitHub.
2. **Step 6.2 (workflow pause/resume):** present a short plan and get the owner's go-ahead.
3. **Before any hosted deployment:** settle OD-14, OD-19 and OD-20.
