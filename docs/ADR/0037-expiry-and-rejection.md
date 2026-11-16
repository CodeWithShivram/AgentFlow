# ADR-0037: Expiring approval requests and rejection choices

- Status: Accepted
- Date: 2026-11-16
- Implements: Phase 6, Step 6.3 (expiration and rejection behaviour); handoff §5.7 (requests store their expiry and response) and §5.8 ("What happens if approved or rejected")
- Builds on: ADR-0029 (recovery sweep), ADR-0034 (escalation), ADR-0035 (requests for people), ADR-0036 (pausing for decisions)
- Owner decisions (2026-11-16):
  - only agents' approval requests expire, after 24 hours by default;
  - an expired request counts as a rejection;
  - an admin who rejects may let the agent finish without the action, or stop the task;
  - a rejection may carry an optional note that only people see.

## Context
- **Undecided requests could wait forever.** An agent's approval request could wait indefinitely. Since ADR-0036, it would also keep an approval-required workflow paused.
- **Rejection had one fixed outcome.** It always meant "retry without the action", even when the admin wanted the task to stop altogether.
- **No reason was recorded.** A rejection carried no reason for the people who come later.

## Decisions

### Expiry
- **When a request expires.** The runner sets `expires_at` when an agent asks for approval. The duration comes from the new `Settings.intervention_approval_ttl_hours` (`INTERVENTION_APPROVAL_TTL_HOURS`, default 24, 1 to 720). The worker passes it to `runner.finish`, whose default is 24 hours.
- **The sweep.** The recovery sweep (every minute, ADR-0029) finds OPEN agent requests past `expires_at`. It expires each one in its own transaction, under the workflow lock, after re-checking that the request is still open and overdue and that the task still waits.
- **What expiry does.** The request becomes EXPIRED (a new status; migration `0026` extends the check), with `{decision: "reject", outcome: "expired"}` and no responder. It is then handled exactly as an admin's plain rejection, by the platform (SYSTEM):
  - the same agent's next attempt is told to finish without the action (`rework.REJECTED_INSTRUCTION`; EXPIRED counts like RESOLVED);
  - the attempt that asked counts, so after the last allowed attempt the task escalates;
  - a workflow paused for the decision resumes (ADR-0036).
- **What never expires.** Plan approvals, analysis questions and escalations do not expire; they wait for people as before.
- **Visibility.** The request shows when it will expire and what happens then (`context.if_expired`).

### Rejection choices
- **`then` on an agent's request.** `POST …/interventions/{id}/reject` takes an optional body `{then: "retry" | "stop", note}`.
  - `retry` (the default) is today's behaviour.
  - `stop` escalates the task for people to reassign, cancel or replan (WAITING_APPROVAL → ESCALATED, USER; reason `APPROVAL_REJECTED`). The MANUAL_RECOVERY request it opens names that reason. The attempt that asked still counts, so a later reassignment continues the numbering.
- **Plan approvals.** They take a note but not `stop` (409 `INTERVENTION_TYPE_MISMATCH`).
- **The note:**
  - limited to 1000 characters;
  - stored in the request's `resolution` and shown to people as plain text;
  - never in events (they carry only `decision`, `outcome` and `then`), never in logs, and never sent to agents or models, whose instructions stay fixed platform sentences (ADR-0032).
- **One code path.** People's decisions and expiry share `engine.decide_agent_action`, so they cannot drift apart.
- **New transition edges:**
  - WAITING_APPROVAL → ASSIGNED for SYSTEM (expiry);
  - WAITING_APPROVAL → ESCALATED for USER (stop).

### REJECTION
The handoff's REJECTION intervention type stays defined but unused. Rejections are decisions recorded on APPROVAL requests, not requests of their own.

## Consequences
- **No indefinite pause.** An approval-required workflow can no longer stay paused forever on an undecided agent request.
- **Expiry never approves.** The action is not taken, and the agent's work carries on without it.
- **Expiry is not tested in the browser.** The browser suite does not exercise expiry, which needs a clock in the past. Integration tests drive it through the sweep.
