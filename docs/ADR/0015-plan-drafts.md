# ADR-0015: Plan drafts — task decomposition and acceptance criteria

- Status: Accepted
- Date: 2026-11-13
- Implements: handoff §1 (structured plan; small, verifiable tasks; DAG), Phase 2, Step 2.3
- Builds on: ADR-0013 (model gateway), ADR-0014 (prompt analysis)
- Owner decisions (2026-11-13):
  - generation starts automatically when the analysis is READY;
  - versioned drafts, applied only on confirmation (Step 2.5);
  - a minimal status UI;
  - at most 25 tasks per draft.

## Context
The analysis (ADR-0014) says what is wanted. The planner turns it into tasks that specialist agents can execute and that verification can check. Step 2.4 adds DAG validation and the plan preview; Step 2.5 adds confirmation, which turns a draft into workflow tasks.

## Decisions

### Trigger
- **Automatic.** When the analysis job finishes READY, it calls `engine.request_plan` in the same transaction. That writes a `workflow.plan` outbox row (identifiers only), sets `plan_status = PENDING` and `plan_request_id`, and appends `PLAN_REQUESTED`.
- **Regenerate.** `POST …/workflows/{id}/plan-drafts` (Member) requests another draft. It is allowed when:
  - the analysis is READY;
  - the workflow is in PLANNING, AWAITING_APPROVAL or READY;
  - no draft is being generated.
  
  Otherwise it returns 409 `PLAN_NOT_REGENERATABLE`.

### Job
`app/planning/plan.py` follows the analysis job:
1. **Claim:** inbox deduplication; only the current request; the analysis must be READY.
2. **Generate:** through the gateway (purpose `plan_generation`, prompt `plan_generation/v1`), up to 3 attempts with backoff. Each attempt is an `llm_usage` row.
3. **Finish:**
   - On success, it inserts the next `workflow_plans` version, sets `plan_status = READY` and appends `PLAN_GENERATED` (version and task count only).
   - Otherwise it sets FAILED and appends `PLAN_GENERATION_FAILED`.
   - A superseded or duplicate delivery changes nothing, and a time-out counts as a failure.

Neither generation nor regeneration changes the workflow's tasks or its `revision`.

### Schema
- **`PlanProposal`** (strict) holds a summary, 1–25 tasks, and up to 10 risks.
- **Each `PlannedTask`** has:
  - a `key` slug and a title (the description is optional);
  - 1–10 required skills, which must be unique and must exist in the catalog;
  - 1–10 deliverables;
  - 1–20 acceptance criteria (`AcceptanceCriterion`, with unique IDs);
  - a priority and `required`;
  - `depends_on`: other keys, with no duplicates and no self-reference.
- **Plan-level rules.** Task keys must be unique, and every dependency must name a task in the draft.
- **Deferred to Step 2.4.** Cycle detection belongs to the DAG validation there.
- **Text safety.** Credential-like text is rejected, as for the analysis.
- **Fits task creation.** A planned task's fields fit `TaskCreateRequest` (tested), so a confirmed draft becomes workflow tasks without translation.

### Prompt
- The validated analysis and the original request go to the model as delimited data (`<analysis>`, `<user_request>`), and the model is told not to follow instructions inside them.
- The workflow's maximum parallel tasks is given as context. Skills are limited to the catalog.

### Storage (`workflow_plans`)
- **Columns:** `version` (unique per workflow), the validated plan as JSON, `task_count` (1–25), the producing `request_id` and `prompt_version`, and `created_at`.
- **Immutable:** a trigger rejects UPDATE.
- **Security:** row-level security with tenant and platform policies. The worker has `SELECT, INSERT`, and the API reads drafts.
- **API:** `GET …/plan-drafts` lists draft summaries (version, task count, prompt version, created). Full content is exposed with the preview in Step 2.4.

### Mock behaviour
- The deterministic default turns each analysis requirement into a parallel task (a "could" requirement becomes optional).
- When there are several, it adds a final consolidation task that depends on all of them.
- It caps plans at 25 tasks. Development and E2E runs therefore show a realistic DAG without API keys.

### UI
The Requirements tab shows the draft status:
- drafting;
- failed, with a Regenerate button for members;
- or "Draft plan version N is ready with M tasks", with Regenerate.

The page polls while a draft is pending.

## Consequences
- **Step 2.4** validates drafts as DAGs (cycles, unreachable work) and shows the full preview. **Step 2.5** confirms a draft into tasks; whether manual tasks are replaced or merged is decided there.
- **Not yet supported:** planner-proposed new agents (handoff §7) and agent selection; they arrive in later phases.
