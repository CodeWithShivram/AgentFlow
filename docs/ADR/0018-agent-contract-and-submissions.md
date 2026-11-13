# ADR-0018: Agent contract and structured submissions

- Status: Accepted
- Date: 2026-11-13
- Implements: Phase 3, Step 3.1 (BaseAgent contract and AgentSubmission schema); handoff §5.3 (task contract), §5.5 (execution), §7 (agent interface)
- Builds on: ADR-0008 (agent registry), ADR-0010 (background execution), ADR-0013 (model gateway), ADR-0017 (plan confirmation)
- Owner decisions (2026-11-13):
  - each attempt's submission is stored in a column on `agent_executions`;
  - `BaseAgent.execute` is synchronous;
  - tasks get a `deliverables` column, filled from confirmed plan drafts;
  - the executions panel shows submissions.

## Context
Until now, the worker ran a placeholder that returned a string. Specialist agents (Step 3.4), tools (3.2) and real models (3.3) need a common, typed interface first. Verification (Phase 5) also needs a structured result to check.

## Decisions

### Task contract (`app/agents/contract.py`)
- **Contents.** `TaskContract` is a strict, frozen model with the handoff §5.3 fields:
  - objective and context;
  - input references, required skills, deliverables and output schema;
  - acceptance criteria and dependency task IDs;
  - allowed tools, timeout and token budget;
  - retry policy (maximum attempts, this attempt's number) and security classification.
- **Source of the data.** The worker builds the contract from platform data only: the task row, its pinned agent version's validated `AgentConfiguration`, and the dependency edges. `claim` snapshots that data inside its transaction; `build_contract` then runs outside it.
- **Allowed tools** are the tools granted to the version that the platform currently enables. Code-execution tools stay disabled (handoff §13); enforcing them at run time is Step 3.2.
- **Timeout.** It is capped at the worker's soft time limit of 600 s (`AGENT_TIME_LIMIT_SECONDS`). A contract never promises more time than the worker enforces.
- **Security classification** is always `INTERNAL` until a classification policy exists.

### Agent interface (`app/agents/base.py`)
- **Methods.** `BaseAgent(configuration)` provides `execute(contract, context)`, `get_capabilities()` and `validate_output(contract, result)`.
  - `execute` is synchronous: agents run in Celery workers with the sync model gateway (ADR-0013), outside any transaction. The handoff's async signature is conceptual.
  - `validate_output` takes the contract, because the checks need its acceptance criteria. It is `@final`: platform-owned checks that agents cannot weaken.
- **Context.** `AgentContext` carries runtime identifiers only (execution, dispatch event, agent, version), never instructions.
- **Mock agent.** `MockAgent` replaces the old placeholder. It is deterministic, calls no model and uses no tools. It claims each criterion as met with evidence that says no real work was done, and lists that as a limitation. Every version runs it until Step 3.4.

### Submission
- **Contents.** `AgentSubmission` (schema version 1) is a strict model:
  - a summary;
  - `outputs`, which is deliverable content keyed by name (at most 100,000 characters of JSON);
  - one `CriterionClaim` per acceptance criterion (`MET` / `PARTIAL` / `NOT_MET` with evidence);
  - assumptions, limitations and open questions.
- **Credential-like text** is rejected in the summary, evidence and list items, as in agent configurations. `outputs` values are not scanned yet.
- **Checks.** `validate_submission` parses the output and requires exactly one claim per contract criterion. It returns issue codes:
  - `SCHEMA_INVALID`;
  - `MISSING_CRITERION_CLAIM`;
  - `UNKNOWN_CRITERION`;
  - `DUPLICATE_CRITERION_CLAIM`.

  It never returns the parser's text, which may echo agent output.
- **Not checked yet.** Conformance of `outputs` to the task's JSON `output_schema` is verification's job (Layer A, Phase 5), so no JSON Schema dependency is added now.
- **Invalid output** is a failed attempt: the execution is FAILED with `INVALID_SUBMISSION` and the codes as its summary. It then follows the existing REWORK_REQUIRED / FAILED path (3 attempts).
- **Claims are only claims.** Agents still never complete tasks: a valid submission moves the task to SUBMITTED, and platform verification decides acceptance.

### Storage (migration `0011_agent_contract`)
- **Submissions.** `agent_executions.submission` (JSONB) holds the validated submission of a successful attempt. A check constraint allows it only when the status is SUCCEEDED. `result_summary` keeps the summary for existing views. Attempts are never overwritten, so a later failed verification keeps the submission (handoff §6).
- **Deliverables.** `workflow_tasks.deliverables` (JSONB list, default `[]`):
  - confirmation (ADR-0017) now copies a draft task's deliverables, which were previously dropped;
  - manual tasks may set up to 20 through the API and the task form (one per line).
- **Permissions.** No new tables: the existing RLS policies and worker grants cover both columns.

### UI
- **Executions panel.** Each successful attempt shows its summary, then the agent's claims labelled "not verified yet" with the criterion text and evidence, then open questions, assumptions and limitations. All of it is plain text.
- **Task details.** They list the task's deliverables.

## Consequences
- **Next steps.** Steps 3.2–3.5 plug tools, the LiteLLM adapter and the specialist agents into this interface without changing the runner's claim → run → finish shape.
- **Not stored.** The contract itself is not persisted per attempt; it can be rebuilt from the task, the pinned version and the dependency edges. Persisting it is a possible later audit improvement.
- **Timeouts.** A version's timeout above 600 s is capped. Per-task Celery time limits are Phase 4 (worker lifecycle).
