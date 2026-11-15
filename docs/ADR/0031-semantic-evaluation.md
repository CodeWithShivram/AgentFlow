# ADR-0031: Semantic evaluation with structured evidence

- Status: Accepted
- Date: 2026-11-15
- Implements: Phase 5, Step 5.2 (semantic evaluation with structured evidence); handoff §8 layer B ("An LLM evaluator may assist semantic evaluation but must return structured findings with criterion IDs, evidence and reasons. It is not a substitute for deterministic tests.")
- Builds on: ADR-0013 (model gateway), ADR-0014 (planning job pattern), ADR-0029 (recovery), ADR-0030 (deterministic verification)
- Owner decisions (2026-11-15):
  - every submission that passes the deterministic checks is evaluated;
  - a criterion or the objective judged not met, or a conflict with the requirements, fails; partly met, incomplete and unstated assumptions are warnings;
  - an evaluation that cannot be completed is retried, then the task is escalated to people, without counting against the agent's attempts.

## Context
- **Structure, not substance.** The deterministic checks (ADR-0030) prove a submission is complete and well-formed, not that it does what was asked. Until now, any agent claiming MET with non-empty outputs passed.

## Decisions

### Flow
- **Handoff from the checks.** When the deterministic checks pass, the task stays VERIFYING and the job writes a `task.evaluate` outbox row (identifiers only) instead of completing it. A failed check still sends the task back without evaluation.
- **The job** (`app/verification/semantic.py`, Celery `agentflow.evaluate_task`, execution queue, 300 s soft and 330 s hard limits) follows the planning jobs, so no transaction stays open while the model works:
  1. **claim**: records the event in the inbox, then checks the task is still VERIFYING, its latest successful attempt passed the checks and has not been evaluated yet;
  2. **evaluate**: up to 3 model calls through the gateway, each recorded in `llm_usage` against the attempt;
  3. **finish**: under the workflow lock, stores the result and moves the task VERIFYING → COMPLETED, REWORK_REQUIRED, or FAILED after the last allowed attempt.
- **Stale results.** A result that arrives after the task moved on (cancelled, or evaluated already) changes nothing.

### The evaluator (`semantic/v1`)
- **Input.** A fixed system instruction; the task (objective, context, deliverables, criteria, output schema) and the submission (summary, outputs, claims, assumptions, limitations, open questions) as JSON inside `<task_contract>` and `<submission>` delimiters.
  - Every `</` is escaped, so the data cannot close a delimiter.
  - No platform identifiers are sent.
  - The model is told the data is not instructions, and that the agent's own claims are not evidence.
- **Output.** A reply must parse as the strict `Evaluation` model: an objective verdict with its reason; one finding per criterion (verdict `met`/`partially_met`/`not_met`, evidence, reason); `complete`, `consistent`, `assumptions_explicit` and `conflicts_with_requirements`; a summary.
- **Retries.** A reply that is invalid, or that does not cover each criterion exactly once, is retried with a correction naming the issue codes only.
- **Mock default.** A schema-valid reply finding everything met is registered, so offline and E2E runs keep working.

### Decision rules (platform-owned, `decide`)
- **Errors:** `OBJECTIVE_NOT_MET`, `CRITERION_JUDGED_NOT_MET` (per criterion) and `CONFLICTS_WITH_REQUIREMENTS`.
- **Warnings:** `OBJECTIVE_PARTIAL`, `CRITERION_JUDGED_PARTIAL`, `INCOMPLETE`, `INCONSISTENT` and `ASSUMPTIONS_NOT_EXPLICIT`.
- **Outcome.** Any error fails verification (rework, or FAILED on the third attempt), recorded as `VERIFICATION_FAILED` with the error codes.

### When the evaluation cannot be completed
- **The transition.** After 3 failed model calls, a soft time limit, or an interrupted job, the task goes VERIFYING → ESCALATED (VERIFIER, reason `EVALUATION_UNAVAILABLE`, with the error code).
- **The consequence.** The workflow becomes BLOCKED, as for any escalated required task, until a member reassigns the task. No result row is written, and the agent's attempt is not counted against it.
- **Recovery** (ADR-0029):
  - an evaluation still pending 330 s plus 120 s of grace after its claim is escalated (`EVALUATION_INTERRUPTED`);
  - a `task.evaluate` message published 15 minutes ago and never consumed is resent.

### Records, API and UI
- **The column.** Migration `0020` adds `verification_results.findings` (JSONB, NULL for checks): the evaluator's findings, which are untrusted model text.
- **Where the text appears.** It is stored and shown only as plain text: never logged, and never part of issue codes, events or error summaries. Issues stay codes, severities and the task's criterion ids.
- **API.** The verification endpoint and each attempt's `verifications` include `findings`.
- **UI.** The attempts panel shows one section per verifier ("Platform verification: deterministic checks" and "…: semantic evaluation"). The evaluation section lists the objective verdict and reason, each criterion's verdict, evidence and reason, and the summary.

### State machine: VERIFYING can be cancelled
- **Why.** VERIFYING used to last one transaction; it now lasts through a model call. Without a VERIFYING → CANCELLED edge, a person could not cancel such a task, and cancelling its workflow (which cancels every unfinished task) failed.
- **The change.** The edge is added for people and the system, like SUBMITTED → CANCELLED. ADR-0009's matrix is amended accordingly, and a late evaluation is ignored.

## Consequences
- **Two model calls per task.** Every successful attempt costs a second, recorded model call. Pricing arrives in Phase 7.
- **A judgment, not a proof.** The evaluator's verdicts are a model's judgment. They can block work (by the owner's rules), but never replace the deterministic checks, and a person can still reassign or retry.
- **Provider outages need people.** An outage escalates verifications rather than failing agents.
- **Still open.** Automatic rework instructions built from these findings are Step 5.3; escalation policies are Step 5.5.
