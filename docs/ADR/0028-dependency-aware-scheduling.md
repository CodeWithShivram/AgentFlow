# ADR-0028: Dependency-aware scheduling and concurrency limits

- Status: Accepted
- Date: 2026-11-14
- Implements: Phase 4, Step 4.4 (dependency-aware scheduling and concurrency limits); handoff §9 (parallel execution, `max_parallel_tasks`)
- Builds on: ADR-0009 (workflow engine and state machine), ADR-0010 (outbox and workers), ADR-0025 (orchestration graph)
- Owner decisions (2026-11-14):
  - members may pre-assign an agent to a task that still waits for its dependencies; it runs when ready;
  - concurrency is limited per workflow only (`max_parallel_tasks`), with no workspace-wide cap.

## Context
- **Already in place (ADR-0009, ADR-0010):**
  - dependencies keep a task PENDING until every dependency is COMPLETED; `reconcile_workflow` then moves it to READY;
  - the worker's claim counts QUEUED and IN_PROGRESS tasks (`EXECUTION_SLOT_STATUSES`) and defers a dispatch when the workflow has no free slot;
  - finishing a task re-dispatches every ASSIGNED task.
- **Missing:**
  - every assignment wrote a dispatch at once, so with a full workflow the outbox filled with dispatches that were only deferred;
  - when a slot freed, which waiting task ran first depended on delivery order, not on task priority;
  - a task could be assigned only once READY, so a person had to come back after each dependency finished.

## Decisions

### Dispatch within free slots, by priority
- **One decision point.** `engine._dispatch_waiting` runs after a task becomes ASSIGNED, after a task leaves an execution slot, and when the workflow resumes. It does nothing unless the workflow is RUNNING.
- **Only free slots are filled.** Free slots are `max_parallel_tasks` minus the active tasks in QUEUED or IN_PROGRESS. The engine writes dispatches for that many ASSIGNED tasks; the rest wait as ASSIGNED, with no outbox row.
- **Order.** `engine.dispatch_order`: highest `priority` first, then oldest `created_at`, then task id, so the order is stable.
- **Once per transaction.** A task gets at most one dispatch per transaction (a per-transaction marker, like the orchestration request), even when several changes in one transaction reach the decision point.
- **The claim still decides.** A dispatched task takes a slot only when a worker claims it, so the claim's existing check stays. A dispatch written before the slot filled (or delivered twice) is deferred, and the next freed slot dispatches again.

### Pre-assignment of waiting tasks
- **Assigning a PENDING task.** `POST …/tasks/{task_id}/assign` on a PENDING task pins the agent version as usual (selection row, skills check, task `revision` bump) and writes `TASK_ASSIGNED`/`TASK_REASSIGNED` with `pre_assigned: true`. The status stays PENDING and nothing is dispatched. It is refused once the workflow is terminal.
- **The scheduler takes over.** When `reconcile_workflow` moves a pre-assigned task to READY, the engine moves it on to ASSIGNED with the SCHEDULER actor (an existing edge), reason "agent pre-assigned", and the decision point dispatches it within the slot limit.
- **Edits clear it.** Changing `required_skills` on a pre-assigned PENDING task clears the pin, because the agent was checked against the old skills; the `TASK_UPDATED` event records `pre_assignment_cleared`.
- **The pinned version runs.** As with any assignment (ADR-0008), the task runs the agent version pinned when it was assigned; deactivating the agent later does not unassign it. A pre-assignment can wait longer, so a person who wants a different agent reassigns the task before it starts.

### Orchestration summary
- `GRAPH_VERSION` becomes `orchestration/v2`: the running summary counts ASSIGNED tasks as "waiting for a free slot" apart from those agents are working on.

### UI
- PENDING tasks in a non-terminal workflow offer "Pre-assign agent…" in the task menu; it opens the same assignment dialog with ranked suggestions.

## Consequences
- **Fewer outbox rows.** A workflow at its limit writes no dispatches for waiting tasks; finishing a task writes one per freed slot.
- **No workspace cap.** Many running workflows can still use many workers at once; a workspace or global cap would be a later decision.
- **Priority is not preemptive.** A higher-priority task waits for a free slot; running tasks are never stopped for it.
- **Stranded executions (OD-17)** still hold their slot until Step 4.5's recovery.
