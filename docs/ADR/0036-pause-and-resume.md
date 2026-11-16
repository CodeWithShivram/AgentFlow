# ADR-0036: Pausing for decisions and resuming safely

- Status: Accepted
- Date: 2026-11-16
- Implements: Phase 6, Step 6.2 (workflow pause/resume). Handoff references:
  - §1: "Pauses for human clarification or approval when policy requires it";
  - critical scenario 10: "Human approval pauses and resumes a workflow";
  - acceptance 12: "Human response is persisted and workflow resumes safely".
- Builds on:
  - ADR-0009 (state machine);
  - ADR-0028 (scheduling on resume);
  - ADR-0034 (the deadline pause);
  - ADR-0035 (requests for people).
- Owner decisions (2026-11-16):
  - only approval-required workflows pause for an agent's approval request;
  - the platform resumes them automatically once it is safe;
  - the deadline rule stays as it is;
  - a person may add an optional note when pausing.

## Context
- **Pausing already worked.** People could pause and resume a workflow (Steps 1.7 and 4.4), and the recovery sweep paused overdue workflows once (ADR-0034).
- **A pause did not say why.** Nothing recorded the reason or the person, so the platform could not tell a pause it may lift from one it must not.
- **Agents' requests only stopped their own task.** Since 6.1, an agent's approval request made just that task wait, while the rest of the workflow ran on, whatever the workflow's policy.

## Decisions

### A paused workflow says why
- **New columns.** Migration `0025` adds `pause_reason`, `paused_by_user_id`, `paused_at` and `pause_note` to `workflows`. A check constraint makes PAUSED equivalent to having a reason and a time.
- **The three reasons:**
  - `USER`: a person, recorded with their note (user text, plain text only);
  - `DEADLINE_PASSED`: the recovery sweep;
  - `AWAITING_DECISION`: the platform, waiting for an admin.
- **The backfill.** Workflows already paused are backfilled from their latest pause event: `DEADLINE_PASSED` when the sweep paused them, otherwise `USER`.
- **Recorded by the engine.** `engine.transition_workflow` sets the fields when pausing and clears them on leaving PAUSED.
  - A platform pause must name its reason; a person's pause defaults to `USER`.
  - Workflow transitions by people now carry `user_id` in their event, and leaving a pause records which pause it was.

### Approval-required workflows stop for agents' requests
- **The pause.** When an agent asks for approval (ADR-0035) in a RUNNING workflow whose `autonomy_policy` is `approval_required`, the runner pauses the whole workflow in the same transaction: SYSTEM, `AWAITING_DECISION`.
- **Autonomous workflows are unchanged.** Only the asking task waits, and other tasks keep running.
- **Running attempts.** As with any pause, attempts already running finish, and nothing new is dispatched.

### Resuming
- **Automatic resume.** When a task leaves WAITING_APPROVAL (an admin decided, or the task was cancelled), `engine.resume_after_decisions` resumes the workflow (SYSTEM, `DECISIONS_MADE`). It does so only if both hold:
  - the workflow is paused for `AWAITING_DECISION`;
  - no agent's request in it is still open.
- **What resuming does.** Resuming reconciles readiness and dispatches by priority within the slot limit (ADR-0028), so the agent's next attempt starts at once.
- **The platform never lifts a person's or the deadline's pause.** A person can take over a platform pause by resuming and pausing again; the decision then leaves the workflow paused.
- **Resuming by hand.** People may resume any pause. Resuming past the deadline is deliberate, and the deadline does not pause the workflow again (ADR-0034, unchanged). Resuming during a decision pause carries on with the other tasks while the request stays open.

### Frontend
- **The banner.** A paused workflow shows who or what paused it, when, the person's note, and what resuming will do.
- **Pausing.** "Pause" now asks for confirmation, with an optional reason.

## Consequences
- **Scenario 10 holds end to end.** In an approval-required workflow, an agent's request pauses the workflow; the admin's decision resumes it and starts the agent's next attempt.
- **Attributed pauses.** A pause is attributable and queryable without reading events.
- **Expiry is still missing.** A request that never gets a decision keeps an approval-required workflow paused. Expiry is Step 6.3.
