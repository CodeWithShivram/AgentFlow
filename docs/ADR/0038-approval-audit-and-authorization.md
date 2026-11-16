# ADR-0038: Approval audit records and per-action authorization

- Status: Accepted
- Date: 2026-11-16
- Implements: Phase 6, Step 6.4 (approval audit records and per-action authorization). Handoff references:
  - §9: "A user approval must be authenticated, authorized, recorded and bound to the specific action. Do not treat a generic approval as permission for unrelated future actions";
  - §13: "Approval permission is distinct from workflow execution permission";
  - critical scenario 11: "Unauthorized approval is rejected".
- Builds on:
  - ADR-0007 (roles);
  - ADR-0008 (only owners grant HIGH-risk tools);
  - ADR-0035 to ADR-0037 (requests, pauses, expiry and rejection).
- Owner decisions (2026-11-16):
  - who may decide follows the agent's risk;
  - the creator of a workflow may decide its requests when their role allows;
  - the history shows on each request and on a workspace page;
  - the audit keeps the rejection note.

## Context
- **Decisions were attributed but not audited.** The request row recorded who decided and how, but it is updated in place and kept neither the decider's role nor exactly what they approved.
- **Every decision needed the same role.** Any admin could approve an action for an agent holding tools that only owners may grant.

## Decisions

### An immutable record per decision
- **The table.** `approval_decisions` (migration `0027`, RLS, worker grants SELECT/INSERT). A trigger refuses updates; rows go only when their workspace is deleted.
- **Contents of a record:**
  - the decision, the rejection choice (`then`) and the person's note;
  - the role the request required;
  - the decider and their role at that moment (both NULL, with `expired`, when nobody decided in time);
  - the asking attempt (`execution_id`);
  - `action_fingerprint`: SHA-256 of the request's id, workflow, task, subject, requested action and context, which is exactly what the decider was shown.
- **One record per request** (unique `intervention_id`).
- **Written by the engine.** `engine.resolve_intervention` writes the record whenever an APPROVAL request is decided, in the same transaction. Every path leads there: the request endpoints, the workflow's own approve and send-back buttons, and expiry.
  - Callers pass the decider's role.
  - A check constraint refuses a person without a role.
  - Withdrawals are not decisions and write no record.
- **Earlier decisions.** Decisions made before the migration have no record.

### Bound to the action, for one attempt
- **One attempt only.** An approved action still reaches only the attempt after the one that asked (ADR-0035). A later attempt never inherits it, and a test covers this.
- **The fingerprint check.** Before passing it on, the runner (`rework.previous_attempt_approvals`) checks that the request's audit record exists, approves, and carries the fingerprint of the request as it is now.
  - A request changed after the decision, or one without a record, passes nothing.
  - The mismatch is logged with identifiers only.

### Per-action authorization
- **The required role.** Every APPROVAL request stores `required_role` when it opens (check constraint; existing ones backfilled):
  - plan approvals need ADMIN;
  - an agent's request needs OWNER when the asking agent version is granted any HIGH-risk tool, and ADMIN otherwise (`runner.approval_role`).
- **Where the role comes from.** It is derived from the platform's own record of the version, never from what the agent says. It follows ADR-0008, where only an owner may grant such tools.
- **Enforcement.** The service refuses a decision from a lower role with 403 `INSUFFICIENT_ROLE`, after the route's ADMIN check, and leaves the request open.
- **Self-approval.** The workflow's creator may decide when their role allows. Records mark it (`by_workflow_creator`), so it shows in the audit.

### Where it shows
- **API:** `GET /workspaces/{id}/approvals` (ADMIN, newest first, optional `workflow_id`) lists records with what was decided. Request responses carry `required_role` and, once decided, `decision_record`.
- **Each request** shows the role it needs and, once decided, who decided, in which role, and the fingerprint.
- **The workspace "Approval history" page** (ADMIN, linked from Requests) lists every record.

## Consequences
- **Phase 6 is complete.**
- **Audited and bound.** Every approval decision is authenticated, authorized for its action, recorded immutably and bound to what was shown and to one attempt.
- **Changing the rules.** Changing which actions need an owner means changing `approval_role`. Requests keep the role they opened with.
