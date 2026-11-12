# ADR-0014: Structured prompt analysis

- Status: Accepted
- Date: 2026-11-12
- Implements: handoff §1 (understand the request; extract requirements and constraints), Phase 2, Step 2.2
- Builds on: ADR-0010 (outbox and workers), ADR-0012 (RLS), ADR-0013 (model gateway)
- Owner decisions (2026-11-12):
  - analysis starts automatically when planning starts;
  - bounded retries, then a recorded failure with a user retry;
  - ambiguities are recorded and shown, not blocking;
  - the UI gets a Requirements panel.

## Context
Before tasks are planned, the platform should understand the request: its objective, requirements, constraints, deliverables, assumptions, and what is unclear. This is the first product feature that calls a model, so it sets the pattern for later planning steps.

## Decisions

### Trigger and transport
- **Request.** `engine.request_analysis`, in the single transition engine, runs when a workflow moves PENDING → PLANNING. In the same transaction, it:
  - writes a `workflow.analyze` outbox row, with identifiers only and never the prompt;
  - sets `analysis_status = PENDING` and `analysis_request_id` to that event;
  - appends `ANALYSIS_REQUESTED`.
- **Delivery.** The relay publishes the row to the `agentflow.analyze_workflow` Celery task, on the execution queue.

### Worker job (`app/planning/analysis.py`)
The job follows the task-execution pattern, so no transaction is open while the model works:
1. **Claim:** inbox deduplication; the delivery must be the workflow's current request and still PENDING. It reads the prompt and settings.
2. **Analyse:** through the model gateway, with up to 3 attempts and backoff (1 s, then 2 s). Provider failures and invalid output are both retried, and each attempt is an `llm_usage` row.
3. **Finish:**
   - On success, it stores the validated analysis in `workflows.normalized_requirements` and sets `READY` with a `REQUIREMENTS_ANALYZED` event. The event records counts and the prompt version, never content.
   - Otherwise it sets `FAILED` with an `ANALYSIS_FAILED` event carrying an error code.
   - A superseded request changes nothing.
   - A worker time-out is recorded as a failure, so an analysis never stays PENDING.

The analysis never changes the workflow's `revision`: it is information beside the plan, so users editing the plan are not interrupted by conflicts.

### Schema and prompt
- **`PromptAnalysis`** (strict, schema version 1) holds:
  - the objective;
  - requirements with IDs and priority `must`, `should` or `could`;
  - typed constraints;
  - deliverables and assumptions;
  - ambiguities, as a question plus its impact;
  - suggested skills, validated against the agent catalog.
  
  IDs are unique, and credential-like text is rejected.
- **The prompt** is `prompt_analysis/v1`, versioned in code.
  - The user's request is placed between `<user_request>` delimiters. The user cannot close the delimiter, and the model is told never to follow instructions inside it.
  - Budget and deadline are given as context.
  - The model must return JSON matching the schema; output is untrusted until it validates.

### Mock behaviour
- A deterministic default for `prompt_analysis` derives a plausible analysis from the request text:
  - the objective is the first sentence;
  - each sentence becomes a requirement;
  - skills come from keywords;
  - an open question is added for very short requests.
- Development and E2E runs therefore work without API keys. Tests script exact replies.

### API and UI
- **API.**
  - `WorkflowResponse` now includes `analysis_status` and `normalized_requirements`.
  - `POST /workspaces/{id}/workflows/{id}/analysis` (Member) requests the analysis again. It is allowed only when the analysis is `FAILED` (or was never requested) and the workflow is in PLANNING, AWAITING_APPROVAL or READY; otherwise it returns 409 `ANALYSIS_NOT_RETRYABLE`.
- **UI.** The workflow page has a Requirements tab:
  - it shows pending, failed with a retry for members, or the analysis, all rendered as plain text;
  - it polls while the analysis is pending.

## Consequences
- **Ambiguities are only displayed.** Clarification requests that pause a workflow arrive in Phase 6.
- **Feeds the planner.** Step 2.3 (task decomposition) will read `normalized_requirements` as its input.
- **Test setup.** Tests that build RUNNING workflows through the engine now see an extra outbox row. Dispatch-focused tests remove it explicitly, and analysis has its own tests.
