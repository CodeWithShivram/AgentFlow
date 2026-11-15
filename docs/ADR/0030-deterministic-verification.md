# ADR-0030: Deterministic verification

- Status: Accepted
- Date: 2026-11-15
- Implements: Phase 5, Step 5.1 (deterministic verification layer); handoff §5.6 (verification record), §8 layer A (deterministic checks) and §5.3 ("only platform verification accepts a task")
- Builds on: ADR-0009 (state machine: VERIFIER actor), ADR-0010 (outbox, inbox, workers), ADR-0018 (agent contract and submissions), ADR-0022 (Developer and QA outputs), ADR-0029 (recovery)
- Owner decisions (2026-11-15):
  - a failed verification sends the task back for rework, and people retry it; a failure on the third attempt fails the task;
  - an agent's NOT_MET claim fails verification, and PARTIAL is a warning;
  - task output schemas are checked with `jsonschema`, now a direct dependency;
  - results show on each attempt in the task's details.

## Context
- **Nothing accepted submissions.** Tasks stopped at SUBMITTED. Only test stand-ins moved them on, so dependent tasks never ran end to end.
- **Unchecked promises.** The submission model said its outputs' JSON Schema conformance "is checked by verification", but nothing checked it.

## Decisions

### When verification runs
- **The request.** The engine writes a `task.verify` outbox row (identifiers only) in the transaction that moves a task to SUBMITTED, so every submission is verified.
- **The job.** `app/verification/job.py`, Celery task `agentflow.verify_task` on the execution queue, runs as one short transaction with no model call:
  1. it records the event in the inbox; a duplicate stops here;
  2. it locks the workflow and checks the task is still SUBMITTED (otherwise the event is stale and nothing changes);
  3. it runs the checks on the task's latest successful attempt and stores the result;
  4. it moves the task SUBMITTED → VERIFYING → COMPLETED, or → REWORK_REQUIRED, or → FAILED after the last allowed attempt, all as the VERIFIER actor.
- **Effects.** Completing a task runs the usual reconciliation: dependents become ready and pre-assigned ones are dispatched (ADR-0028), and the workflow completes when its required tasks have.
- **No stranding.** The inbox row and the outcome commit together, so a worker lost mid-way leaves nothing behind. A `task.verify` message the broker loses is resent by the recovery sweep after 15 minutes, as with dispatches (ADR-0029).

### The checks (`app/verification/deterministic.py`, version `deterministic/v1`)
- **Pure and versioned.** The checks are pure functions over platform data: the task's contract fields, the attempt's stored submission and the agent version's configuration. Changing a rule means a new version.
- **In order:**
  - **submission:** the stored submission still parses;
  - **criteria_coverage:** exactly one claim per acceptance criterion; claims for unknown criteria are counted, but their ids are not stored;
  - **criteria_claims:** NOT_MET is an error and PARTIAL a warning. MET stays unverified, because this layer cannot judge content (Step 5.2);
  - **deliverables:** every deliverable has non-empty content in `outputs`;
  - **output_schema:** `outputs` matches the task's expected JSON Schema. An invalid or unusable schema (e.g. a remote `$ref`, which is never fetched) is an error. At most 20 mismatches are listed, by schema location;
  - **runtime_outputs:** Developer and QA outputs fit their models, and QA test cases cover only known criteria;
  - **platform_note:** a runtime's platform note is present unless its version grants a tool that makes it untrue.
- **Skipping.** A check that does not apply is recorded as skipped. If the submission cannot be read, every later check is skipped.
- **No agent text in issues.** An issue is a code, a severity, and the criterion, deliverable or schema location it concerns, all task data. Validator messages, which quote outputs, are never stored.

### Records and API
- **The table.** Migration `0019` adds `verification_results`: one row per verifier per attempt (unique `execution_id`, `verifier`), with the checks, the issues and the outcome (PASSED or FAILED). It is append-only (a trigger rejects UPDATE) and under RLS; the worker may SELECT and INSERT.
- **Events.** VERIFICATION_STARTED and TASK_COMPLETED, or VERIFICATION_FAILED with the error codes; their payloads name the verification and the verifier.
- **API.**
  - `GET …/workflows/{id}/tasks/{task_id}/verification` (Viewer) lists results, newest first.
  - Each attempt in `…/executions` carries its `verifications`.
  - The attempts panel shows Passed or Failed, with each issue in plain words.

### Related changes
- **Orchestration.** `GRAPH_VERSION` is `orchestration/v3`: the awaiting-verification summary no longer says verification "arrives in Phase 5".
- **Workflow page polling.** It used to stop as soon as nothing was active, before the reassessment that follows the workflow's completion arrived, which left a stale Next-step banner. The page now keeps polling for its existing five reassessment checks while the stored assessment is older than the latest event.

## Consequences
- **Workflows run to completion without people.** Dependent tasks run end to end, with no manual step.
- **Passing proves structure, not quality.** A submission with all MET claims and the required outputs passes, so with the mock provider every run passes. Semantic evaluation (Step 5.2) is the layer that judges content.
- **Rework is manual for now.** A failed verification waits for a member to retry. Corrective instructions and automatic bounded retries come in Step 5.3.
