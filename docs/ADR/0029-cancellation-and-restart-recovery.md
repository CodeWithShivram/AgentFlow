# ADR-0029: Cooperative cancellation and restart recovery

- Status: Accepted
- Date: 2026-11-14
- Implements: Phase 4, Step 4.5 (cancellation and restart recovery); resolves OD-17; handoff §6 ("Cancellation must be cooperative and persisted", "Retry transient infrastructure failures with bounded backoff", "Never loop indefinitely"), §15 acceptance 12 and 15 ("User cancellation is persisted", "Worker restart does not lose committed tasks")
- Builds on: ADR-0010 (outbox, inbox, workers), ADR-0018 (agent runtime), ADR-0027 (outbox hardening), ADR-0028 (scheduling)
- Owner decisions (2026-11-14):
  - a lost attempt is retried automatically, bounded by the three attempts per task;
  - lost workers are found by a time-based sweep;
  - a cancelled agent stops at its next model or tool call;
  - the sweep also resends lost dispatches and fails interrupted planning jobs.

## Context
- **Cancellation was persisted, not cooperative.** Cancelling a task or workflow moved the task to CANCELLED, but a running agent kept working (and calling models) to the end, and `finish` then discarded its result.
- **Claimed work could be stranded.** Consumers record each event in the inbox when they claim it. If a worker dies after the claim commits, the redelivered message is skipped as a duplicate:
  - a task stays IN_PROGRESS for good and keeps its execution slot (OD-17);
  - an analysis or plan request stays PENDING, and the user cannot retry it.
- **Lost messages.** Redis is transport only. If the broker loses a published dispatch, the task stays ASSIGNED with nothing coming.

## Decisions

### Cooperative cancellation
- **The guard.** `runner.run_agent` wraps the attempt's model gateway and gives the tool gateway a `before_call` hook. Before every model or tool call, an `AttemptGuard` checks that the attempt is still current: its execution is RUNNING and its task IN_PROGRESS (`runner.attempt_is_current`, one indexed read).
- **Stopping.** When the attempt is no longer current, the call is not made (no `llm_usage` or `tool_invocations` row) and `AttemptCancelledError` is raised. The guard stays stopped. The runner raises the error even if the agent caught it, ahead of any tool violation.
- **Recording.** The worker records the outcome as a failure, and `finish` handles it as before: a task no longer IN_PROGRESS marks the attempt CANCELLED; an attempt already closed by recovery is left as it is.
- **What counts as current.** Pausing keeps attempts current, so running attempts finish while paused, as before. Cancelling the task or the workflow, or recovering the attempt, stops it.
- **Database errors.** If the check cannot reach the database, the attempt goes on, and `finish` decides with the committed state.
- **Agent context.** `AgentContext.models` is typed as the `ModelCaller` protocol, satisfied by the gateway and by the runner's guarded wrapper.

### Recovery sweep
- **Schedule.** Beat runs `agentflow.recover` every 60 s on the maintenance queue (`app/execution/recovery.py`, as the restricted worker role).
- **Transactions.** Each item is handled in its own short transaction, under the workflow lock and through the engine, so every status change writes its event.
- **Lost attempts.**
  - **Detection.** An execution still RUNNING `LOST_ATTEMPT_AFTER` after it started (the hard time limit of 660 s plus 120 s of grace, about 13 minutes) is lost: a live prefork worker would have killed it by then. A partial index on running executions serves the scan (migration `0018`).
  - **Closing the attempt.** It is marked FAILED with `WORKER_LOST`. If its task was cancelled meanwhile, it is marked CANCELLED instead.
  - **Automatic retry.** The task goes IN_PROGRESS → REWORK_REQUIRED (SYSTEM, reason `WORKER_LOST`), then → ASSIGNED (SCHEDULER, `automatic_retry: true`) with the same pinned version. Its `attempt_count` goes up as a member's retry would, and the scheduler dispatches it within the slot limit (ADR-0028).
  - **Bound.** The lost attempt counts toward the three per task, so a lost third attempt FAILS the task instead.
- **Lost dispatches.**
  - **Detection.** An ASSIGNED task in a RUNNING workflow whose latest dispatch was published more than 15 minutes ago, and never claimed (no inbox row), is dispatched again, highest priority first, within the free slots.
  - **Safe duplicates.** A dispatch that was consumed (claimed, deferred or skipped) is never resent. A late original delivery finds the task moved on and is skipped.
- **Interrupted planning jobs.**
  - **Detection.** An analysis or plan request still PENDING `INTERRUPTED_JOB_AFTER` (the 330 s hard limit plus 120 s) after its job claimed it is interrupted.
  - **Outcome.** It is marked FAILED with `ANALYSIS_INTERRUPTED` or `PLAN_GENERATION_INTERRUPTED`, through the same failure path as the jobs (`record_failure`), so the existing retry applies.
- **No new event types.** Status changes use the existing events with reason codes. A resent dispatch is a new outbox row and a log line, not a workflow event.

## Consequences
- **Detection takes up to about 13 minutes.** A crashed attempt keeps its slot until then. Heartbeats would be faster, at the cost of more writes and a thread in the worker.
- **Windows solo pool.** It does not enforce time limits, so an attempt that is only slow (over 13 minutes) can be recovered while it still runs. Its agent then stops at its next call, and its late result is discarded. Linux prefork workers enforce the limits, so a live attempt is never recovered there.
- **A retry costs an attempt.** An agent that crashes its worker every time fails after three attempts; it never loops.
- **Resends can duplicate.** If the broker only delays a dispatch past 15 minutes, both deliveries arrive; the second is skipped. Delivery stays at least once, never exactly once.
