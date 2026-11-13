# ADR-0017: Plan confirmation before execution

- Status: Accepted
- Date: 2026-11-13
- Implements: Phase 2, Step 2.5 (user plan confirmation before execution); handoff §16 journey step 4 ("User can inspect the plan before execution")
- Builds on: ADR-0009 (plan editing rules), ADR-0015 (drafts), ADR-0016 (DAG validation)
- Owner decisions (2026-11-13):
  - when tasks exist, the user chooses whether the draft replaces them or is added;
  - confirmation creates tasks only, and the existing approval and ready actions come next;
  - warnings need explicit acknowledgement, and errors always block.

## Decisions

### Endpoint
- **Request.** `POST /workspaces/{id}/workflows/{id}/plan-drafts/{version}/confirm` (Member). The body is `{revision, existing_tasks?: "replace" | "append", acknowledge_warnings}`.
- **Transaction.** It runs under the workflow row lock and checks the caller's `revision`; a stale revision gets the usual `WORKFLOW_REVISION_CONFLICT`.
- **Refusals:**

| Situation | Response |
|---|---|
| Unknown version | 404 `PLAN_DRAFT_NOT_FOUND` |
| The draft re-validates with errors (ADR-0016) | 409 `PLAN_DRAFT_INVALID` |
| Warnings without `acknowledge_warnings: true` | 409 `PLAN_WARNINGS_NOT_ACKNOWLEDGED` (the codes are in `details`) |
| Tasks exist and `existing_tasks` was not given | 409 `EXISTING_TASKS_MODE_REQUIRED` |
| The workflow is not plan-editable (RUNNING or later) | 409 `PLAN_NOT_EDITABLE` |

- **Revalidation.** The draft is re-validated against the workspace's current active agents at confirmation time, as in the preview.

### Effect
- **Plan editing rules (ADR-0009).** Confirming is a plan edit. In AWAITING_APPROVAL or READY it sends the workflow back to PLANNING, which invalidates the approval.
- **Replace** removes each active task the same way manual removal does: CANCELLED, `removed_at` set, dependency edges deleted, a `TASK_REMOVED` event. The tasks stay as history. **Append** keeps them.
- **New tasks.** Each draft task becomes a PENDING task with its title, description, skills, acceptance criteria, priority and required flag. Its `TASK_CREATED` event names the plan version and task key.
- **Dependencies.** Draft keys are mapped to the new task IDs, and each dependency is created with a `DEPENDENCY_ADDED` event. New tasks only depend on new tasks, and drafts are acyclic, so no cycle can form with existing tasks.
- **Record.** `workflows.confirmed_plan_version` records the draft. A `PLAN_CONFIRMED` event records the version, mode, created and removed counts, and acknowledged warning codes. The revision is incremented.
- **What happens next.** Confirmation never starts work. The user then uses the existing Submit for approval / Mark ready and Start actions, so the approval rules are unchanged. The platform task limit (200 per workflow) still applies.

### UI
- **Plan draft tab.** Members see "Confirm this draft" while the plan is editable, and only for drafts without errors. A confirmed version is badged and offers "Confirm again".
- **Dialog:**
  - it states how many tasks will be created, and that work starts only after approval and start;
  - it notes when planning will reopen;
  - it asks Replace or Add when tasks exist;
  - it lists warnings with an "I understand" box that must be ticked.
- **Afterwards.** Success switches to the Tasks tab. A revision conflict opens the standard conflict dialog.

## Consequences
- **Phase 2 is complete.** A request is analysed, drafted into a validated plan, inspected and confirmed before any execution.
- **Next phases.** Agent selection and assignment (Phase 3/4) and clarification requests for open questions (Phase 6) build on confirmed tasks.
