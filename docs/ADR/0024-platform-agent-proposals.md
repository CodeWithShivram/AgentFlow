# ADR-0024: Platform agent proposals with admin approval before activation

- Status: Accepted
- Date: 2026-11-14
- Implements: Phase 3, Step 3.7 (agent proposal workflow with admin approval before activation); handoff §2 ("the Main Orchestrator may propose a new specialist when a capability is missing … require administrator approval before activation"), §7 (dynamic agent creation), §14 screen 6 (permission review)
- Builds on: ADR-0008 (proposals, approval, audit), ADR-0017 (plan confirmation), ADR-0023 (selection)
- Owner decisions (2026-11-14):
  - proposals are filed automatically when a draft is confirmed;
  - the proposed agent is a safe template, with no model involved;
  - the Proposals page shows a per-tool permission review.

## Context
- **What Step 1.6 already built (ADR-0008):** separate frozen proposals; approval only by an ADMIN who isn't the author, re-validated against the catalog and the reviewer's authority (HIGH-risk tools need the OWNER); an **INACTIVE** agent on approval; an append-only audit; a Proposals page.
- **What was missing:**
  - the platform proposing a specialist itself;
  - the handoff's "permission review" on the page.
- **Where the gap really is.** The six built-ins together cover every catalog skill. So a missing capability only arises when **no single active agent has all the skills a task needs**: the ADR-0023 hard filters then leave no eligible candidate, and the task cannot be assigned.

## Decisions

### When the platform proposes
- **The trigger.** `confirm_plan_draft` checks each new task's skill set against the selection rules, once per distinct set, in the confirmation transaction. It runs only when the workflow's existing **"Allow agent proposals"** setting (`allow_dynamic_agent_proposals`) is on. A set with no eligible agent becomes a `Gap`.
- **Filing.** `agents.propose_specialists` files one PENDING proposal per gap, skipping:
  - sets that already have a pending proposal in the workspace;
  - sets whose generated name is already an agent's, because that specialist exists and may only need activating.
- **The record.** `PLAN_CONFIRMED` records `agent_proposals_created`. Each proposal gets a `PROPOSAL_SUBMITTED` audit row with no actor and `details.source = "platform"`.

### What is proposed: the safe template
- **Built deterministically, with no model:**
  - name `Specialist: <skills>`, category `custom`;
  - capabilities: exactly the task's skills;
  - the General runtime's instructions (`builtin://general/v1`), so an approved agent can run (ADR-0021);
  - **no tools** (least privilege);
  - default model and limits;
  - a rationale naming the task.
- **What the platform never does.** It never proposes tools, permissions or instruction text, so it cannot escalate privileges.
- **Authorship.** `requested_by` is NULL (platform). The rule that the reviewer isn't the requester still holds, so any ADMIN may decide. Approval and rejection are unchanged: an approved agent starts INACTIVE, and activation is a separate decision.

### Provenance (migration `0014_platform_agent_proposals`)
- **Links.** `agent_proposals.source_workflow_id` and `source_task_id` (nullable) link a platform proposal to the task that had no suitable agent.
- **Keys and checks.** Composite foreign keys keep the links inside the proposal's workspace, and a check requires a workflow whenever a task is set. The links are added to the freeze trigger, so they can't change after submission.

### Permission review UI
- **Catalog field.** The catalog API adds `usable_now` per tool (implemented in the registry and enabled, ADR-0019).
- **On each proposal:**
  - its origin ("by the platform", with a link to the source workflow);
  - a **Permission review** of every requested tool: access, risk (low/medium/high), whether it is usable now or "grants a permission only", and "Approving needs the workspace owner" for HIGH-risk tools;
  - or "Requests no tools."

  The server enforces all of these again on approval and at run time.

## Consequences
- **Phase 3 is complete.** Agents run behind contracts, tools are gated, models are real behind LiteLLM, selection is explainable, and missing specialists are proposed. Nothing becomes active without an admin's approval and a separate activation.
- **No auto-assignment.** An approved and activated specialist is not assigned automatically: people assign, guided by the ADR-0023 suggestions.
- **Possible later work.** Model-written proposals with richer instructions could follow, but they would need instruction text to be stored and reviewed, which is not needed now.
