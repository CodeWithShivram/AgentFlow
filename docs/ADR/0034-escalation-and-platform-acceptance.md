# ADR-0034: Escalation policies and platform-only acceptance

- Status: Accepted
- Date: 2026-11-15
- Implements: Phase 5, Step 5.5 (escalation policies; only platform verification accepts tasks); handoff §6 ("Escalate after retry/reassignment policy is exhausted"), §9 "Escalate when" list, §15 acceptance 9 ("Repeated failure is escalated") and §5.3 ("only platform verification accepts a task")
- Builds on: ADR-0009 (state machine), ADR-0029 (recovery), ADR-0030 to ADR-0033 (verification, evaluation, rework, replanning)
- Owner decisions (2026-11-15):
  - exhausted attempts escalate to people instead of failing;
  - policy failures escalate;
  - a workflow pauses when its deadline passes;
  - acceptance is enforced by the engine and by the database.

## Context
- **Exhausted attempts.** A third failed attempt made the task FAILED, which failed the whole workflow when the task was required.
- **Policy failures** (ADR-0032) waited silently in REWORK_REQUIRED.
- **Deadlines** were stored but never acted on.
- **Acceptance rested on the actor rule alone.** "Only platform verification accepts tasks" relied only on the state machine's actor rule (VERIFYING → COMPLETED is VERIFIER-only).

## Decisions

### Escalation (to ESCALATED, for people to decide)

| When | Where | Reason recorded |
|---|---|---|
| The third attempt fails (execution failure, failed checks or failed evaluation) | runner, deterministic checks, semantic evaluation | the failure's code |
| A lost worker's last attempt | recovery sweep | `WORKER_LOST` |
| A policy failure: tool permission or call limit, missing runtime, unusable output schema | `rework.schedule_rework` | `POLICY_FAILURE` with the codes |
| The evaluation cannot be completed (ADR-0031, unchanged) | semantic evaluation | `EVALUATION_UNAVAILABLE` |

- **Effect on the workflow.** A required escalated task blocks the workflow (ADR-0009), and the Next-step banner names it (ADR-0033).
- **What people can do:**
  - reassign the task, which is a new attempt with any agent, beyond three if they choose; a failure of such an attempt escalates again at once;
  - cancel it;
  - replan.
- **No automatic FAILED.** No attempt-related path marks a task FAILED automatically any more.
- **Attempt numbering fix.** Reassigning an escalated (or reworked) task now sets `attempt_count` to the larger of the counted attempts (plus the failed one, for a rework retry) and the task's highest recorded attempt number. Before, an escalated task whose last attempt was not counted (evaluation unavailable) would have reused that attempt's number and broken the unique (task, attempt) constraint at the next claim.

### Deadlines
- **The pause.** The recovery sweep (every minute) pauses a RUNNING workflow whose `deadline` has passed: SYSTEM actor, reason `DEADLINE_PASSED`. No new attempts start, and running ones finish.
- **Only once.** A workflow already paused for its deadline is never paused again, so people can deliberately resume it, or replan or cancel it.
- **Budget limits** still wait for Phase 7's cost tracking.

### Only platform verification accepts tasks
- **Engine guard.** `transition_task` refuses COMPLETED, whatever the actor, unless the task's latest successful attempt has PASSED results from both the deterministic checks and the semantic evaluation (`ACCEPTING_VERIFIERS`, matched by verifier kind so that new rule versions count). The semantic job flushes its result before completing the task for this reason.
- **Database guard.** Migration `0023` adds a trigger on `workflow_tasks` (insert, or update of `status`) that enforces the same rule for every role, including direct SQL. A bug, a script or a future code path therefore cannot complete a task without platform verification.
- **The tests follow suit.** Tests that need completed tasks run the real verification jobs: the Step 4.4 stand-in and the replan tests' SQL setup were replaced.

## Consequences
- **Workflows rarely fail by themselves now:** they block and wait for people. A workflow can still fail when a person marks an escalated task failed (an engine path that exists, with no UI yet) or cancels the workflow.
- **The deadline is a soft stop.** Running attempts finish after it.
- **Changing what accepts a task needs a migration** (the trigger) as well as a code change. That is intended.
