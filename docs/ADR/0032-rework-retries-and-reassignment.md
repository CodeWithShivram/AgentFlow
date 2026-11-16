# ADR-0032: Rework instructions, bounded retries and reassignment

- Status: Accepted
- Date: 2026-11-15
- Implements: Phase 5, Step 5.3 (rework instructions, bounded retries and reassignment); handoff §6 recovery defaults ("Maximum attempts per task: 3 total attempts. Maximum reassignment: 1.", "Do not blindly retry deterministic validation failures; attach corrective instructions.") and §8 outcomes REWORK and REASSIGN
- Builds on: ADR-0018 (task contract), ADR-0021 (built-in runtimes), ADR-0023 (agent selection, amended here), ADR-0029 (recovery), ADR-0030 and ADR-0031 (verification)
- Owner decisions (2026-11-15):
  - failed attempts are retried automatically, within the three attempts;
  - the platform reassigns a task once, to another eligible agent, after two failures by the same agent;
  - every failure is retried except tool-policy violations and a missing runtime (and, by the same reasoning, a task schema that cannot be applied);
  - corrective instructions are platform text only.

## Context
- **Retries were manual and blind.** A failed attempt or verification left the task REWORK_REQUIRED for a member to retry, and the retry got the same contract as before, with nothing about what went wrong.

## Decisions

### When something fails: `schedule_rework` (`app/verification/rework.py`, policy `retry/v1`)
- **Where it runs.** It is called in the same transaction as the failure, under the workflow lock, right after a task moves to REWORK_REQUIRED. The callers are the runner (a failed attempt), the deterministic checks, the semantic evaluation and the recovery sweep (a lost worker).
- **The third attempt** is still never retried: the failure transition itself moves the task to FAILED, as before.
- **Waits for people** when:
  - any failure code is one a retry would repeat: `TOOL_PERMISSION_DENIED`, `TOOL_CALL_LIMIT_EXCEEDED`, `AGENT_RUNTIME_UNAVAILABLE`, `OUTPUT_SCHEMA_INVALID` or `OUTPUT_SCHEMA_UNUSABLE`;
  - or the workflow is not executing.

  The task stays REWORK_REQUIRED, and members retry or reassign it as before.
- **Reassigns** when all of these hold:
  - the last two attempts were by the task's current agent;
  - both failed for the agent's own reasons (any failure except `WORKER_LOST`);
  - the task has had no reassignment yet (`MAX_REASSIGNMENTS = 1`, counting reassignments by people too);
  - another eligible agent exists.

  The scheduler then assigns the next attempt to the best-ranked other eligible agent under the selection rules (ADR-0023). It stores the server-computed `agent_selections` row with `decided_by_user_id` NULL, and writes `TASK_REASSIGNED` with the previous and new agent and the selection.
- **Otherwise retries.** The scheduler moves REWORK_REQUIRED → ASSIGNED with the same pinned version (`TASK_ASSIGNED`, `automatic_retry`, `policy`), and the attempt is dispatched within the slot limit.
- **Attempt count.** Either way, the task's `attempt_count` goes up as a member's retry would.

### Corrective instructions (`instructions_for`)
- **Built at claim.** When the runner claims an attempt after the first, it builds instructions from the previous attempt: its error code (e.g. `INVALID_SUBMISSION`, `EXECUTION_TIMED_OUT`) and its verification errors (warnings are left out).
- **Fixed wording.** Each code maps to a fixed sentence. Criteria are named by id and deliverables by position in the contract, never by their text. They are deduplicated and capped at 20.
- **Some failures get none.** Transient failures (lost worker, model call failed) need no correction.
- **Where they go:**
  - stored on the new attempt (`agent_executions.rework_instructions`, migration `0021`);
  - on its task contract (`contract_version` 2, `rework_instructions`);
  - for model agents, in a separate platform message after the delimited contract: "Your previous attempt (attempt N) was not accepted. Address each point in this attempt: …". They go there because the common rules tell models never to follow instructions found inside the contract.
- **Unchanged.** No user, agent or model text is passed on, and the immutable instruction texts (ADR-0021) are unchanged.
- **Shown** on each attempt in the attempts panel ("Corrective instructions given").

### ADR-0023 amended
"People decide; the platform only suggests" now has one exception: the automatic reassignment above. It is bounded (once per task), uses the same versioned rules and stores the same rationale with no user, and members can still reassign or cancel the task at any time.

### Access
The worker may now INSERT into `agent_selections` (append-only, platform RLS policy), to record platform reassignments.

## Consequences
- **Workflows recover without people** from ordinary failures, up to three attempts. A task that keeps failing ends FAILED, as before; escalation policies instead of FAILED are Step 5.5.
- **More model cost.** Automatic retries spend model calls without a person deciding. They are bounded by the three attempts, and every call is recorded.
- **Instructions are generic by design.** They tell the agent what failed, not how to fix it. The semantic evaluator's reasons stay in the attempt's verification for people to read.
- **Follow-up.** The selection rules' "history" factor is still not scored (`selection/v1`). Scoring it from verification outcomes needs a new rules version.

## Amendment (Step 5.5, 2026-11-15)
Policy failures no longer wait in REWORK_REQUIRED, and a third failed attempt no longer ends FAILED: both escalate the task to people (ADR-0034).
