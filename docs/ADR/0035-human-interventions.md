# ADR-0035: Requests for people (clarifications, approvals, escalations)

- Status: Accepted
- Date: 2026-11-16
- Implements: Phase 6, Step 6.1 (clarification and approval request model and API); handoff §5.7 (human interventions: CLARIFICATION, APPROVAL, REJECTION, MANUAL_RECOVERY), §9 (agents may ask for approval of a sensitive action) and §15 ("A person can answer, approve or reject")
- Builds on: ADR-0009 (state machine and engine), ADR-0014/0015 (analysis and plan drafts), ADR-0018 (agent contract), ADR-0025 (orchestration), ADR-0032 (rework), ADR-0034 (escalation)
- Owner decisions (2026-11-16):
  - four sources: analysis questions, plan approval, agents' approval requests and escalated tasks;
  - answers feed the next plan or replan draft, and nothing pauses for a question;
  - members answer, admins approve or reject, and every response is attributed;
  - a workspace "Requests" inbox, plus "Needs your input" on the workflow page;
  - an agent's approval request is followed by a new attempt either way, and that attempt counts; expiry is Step 6.3.

## Context
- **Questions went nowhere.** The analysis listed open questions that nobody could answer.
- **Approval was a status, not a record.** Plan approval was only the workflow status AWAITING_APPROVAL, with no record of who decided.
- **Agents had no approval channel.** An agent had no way to ask before taking a sensitive action. The state machine already had `IN_PROGRESS → WAITING_APPROVAL` for this.
- **Escalations had no inbox.** Escalated tasks (ADR-0034) were visible only on their workflow.

## Decisions

### One table, opened by the platform
`human_interventions` (migration `0024`, RLS like every workspace table, worker grants SELECT/INSERT/UPDATE):
- **Classification:** `type` is the handoff's kind; `subject` says what the request is about (`analysis_question`, `plan_approval`, `agent_action`, `task_escalation`).
- **Lifecycle:** `status` is OPEN, RESOLVED (a person answered, decided or acted) or WITHDRAWN (the platform closed it because the work moved on). A check constraint ties OPEN to an empty `resolved_at`.
- **Content:** `requested_action` and `context` hold the question or proposed action, with the reason, risks and consequences. They may be model or agent text, so they are shown as plain text only and never logged or copied into events.
- **Who asked:** `requested_by` is SYSTEM, AGENT (with the agent id) or USER.
- **Outcome:** `resolution` holds `{answer}`, `{decision}` or `{outcome}`, and `resolved_by_user_id` names the responder (NULL only for platform closes).
- **One request per source.** A unique `(workflow_id, source_key)` means retried code never opens a duplicate. Keys:
  - `clarification:<analysis request>:<ambiguity>`;
  - `plan_approval:<revision>`;
  - `agent_action:<execution>`;
  - `escalation:<task>:<task revision>`.
- **Opening and closing.** Requests are opened and closed only by `app/workflows/engine.py`, in the transaction of the change that causes them, with `INTERVENTION_OPENED` / `INTERVENTION_RESOLVED` events (identifiers, type, decision or outcome; never text).

| Source | Opened when | Closed when |
|---|---|---|
| Analysis question (CLARIFICATION) | a ready analysis lists ambiguities (one request each) | a member answers; a newer analysis withdraws the old questions |
| Plan approval (APPROVAL) | the workflow enters AWAITING_APPROVAL | an admin approves (→ READY) or rejects (→ PLANNING), here or with the workflow's own buttons; otherwise withdrawn when the workflow leaves AWAITING_APPROVAL |
| Agent's action (APPROVAL) | a submission carries `approval_request`; the task goes to WAITING_APPROVAL | an admin approves or rejects; withdrawn if the task is cancelled |
| Escalated task (MANUAL_RECOVERY) | a task reaches ESCALATED | someone reassigns, cancels or replans it (resolved, attributed to that person) |

Reaching a terminal workflow status withdraws everything still open.

### Answers reach the planner
- **Collected at claim time.** The plan job collects the answered questions of the current analysis when it claims (`plan.answered_clarifications`).
- **Delimited.** It passes them to the model in a `<clarifications>` section, delimited like the analysis and the request, with one rule in the system prompt: plan by the answer where it settles a question.
- **New prompt versions:** `plan_generation/v2` and `replan/v2`.
- **No automatic replanning.** Answering changes no status and starts no draft. People regenerate the plan (or replan) when they want the answers used.

### Agents asking for approval
- **The request.** `AgentSubmission.approval_request` (`ApprovalRequest`: action ≤ 1000 characters, reason, risks, consequences; strict like the rest) is optional.
- **On submission.** When it is present, the runner records the attempt as SUCCEEDED, moves the task to WAITING_APPROVAL (no verification) and opens the request. The request context states what each decision leads to.
- **Admin decision.** Either way, the service closes the request and starts a new attempt by the same agent (WAITING_APPROVAL → ASSIGNED, USER; new edge):
  - **approve:** the next contract (`contract_version` 3) lists the action in `approved_actions`. The action is the agent's own text, so it stays inside the delimited contract, and model agents get one fixed platform sentence after it;
  - **reject:** the next attempt gets the fixed instruction `rework.REJECTED_INSTRUCTION`.
- **The asking attempt counts.** If it was the last one allowed (`MAX_ATTEMPTS_PER_TASK`, now in `state_machine.py` so the API can apply it), the decision escalates the task instead (WAITING_APPROVAL → ESCALATED, SYSTEM; new edge). People then resolve the escalation by acting on the task.
- **No expiry yet.** Requests do not expire in 6.1; `expires_at` is reserved for Step 6.3.

### Who may respond
- **VIEWER:** reads requests.
- **MEMBER:** answers clarifications.
- **ADMIN:** approves or rejects.
- **Escalations** have no endpoint of their own: they are resolved by the task actions that already exist, and `assign_agent`, `cancel_task` and plan removals now pass the user id so the resolution is attributed.
- **Endpoints** under `/workspaces/{id}/interventions`:
  - `GET` (filters: `status`, `workflow_id`);
  - `GET /{id}`;
  - `POST /{id}/answer`;
  - `POST /{id}/approve`;
  - `POST /{id}/reject`.
- **Locking.** Each response locks the workflow, then the request, and refuses closed ones (409 `INTERVENTION_CLOSED`) and responses of the wrong kind (409 `INTERVENTION_TYPE_MISMATCH`).

### Orchestration
- **Graph v5.** `orchestration/v5` gives tasks in WAITING_APPROVAL their own next step, `approve_actions` (people), instead of counting them as blocked.

## Consequences
- **One inbox for the workspace.** The sidebar's "Requests" page lists open, resolved and withdrawn requests. Each workflow page shows its open ones under "Needs your input".
- **Attributed plan approval.** Plan approval now has an attributed record whichever button is used.
- **Prompt and contract versions changed** (planner v2, contract v3, graph v5). Tests pinning them were updated.
- **No unauthorised action.** An agent can now pause its own task for a person's decision, but cannot act on its request until an admin approves. The approval itself grants no tools: it only tells the next attempt the action is approved.
