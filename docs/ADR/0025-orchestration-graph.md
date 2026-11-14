# ADR-0025: LangGraph orchestration graph (assess and report)

- Status: Accepted
- Date: 2026-11-14
- Implements: Phase 4, Step 4.1 (LangGraph workflow state and nodes); handoff §3 (LangGraph for stateful orchestration and conditional routing), §4 (an orchestration layer that decides next steps but changes state only through services)
- Builds on: ADR-0009 (engine), ADR-0010 (outbox, workers)
- Owner decisions (2026-11-14):
  - the graph assesses and reports, with no new automatic work;
  - its assessment shows as a workflow page banner.

## Decisions

### Dependency
- **Version.** `langgraph` 1.2.22, locked in `uv.lock`. It brings `langchain-core`, `langgraph-checkpoint`, `langgraph-prebuilt`, `langgraph-sdk` and `langsmith`, plus small libraries.
- **Where it loads.** Only workers import it, through the orchestration job. The API process loads neither LangGraph nor LangSmith (verified).
- **LangSmith off.** LangSmith can send traces to an external service when tracing environment variables are set. `app/orchestration/graph.py` calls `langsmith.configure(enabled=False)`, which disables tracing for the process whatever the environment (tested with the variables set).

### Graph (`app/orchestration/graph.py`, `orchestration/v1`)
- **State.** `OrchestrationState` holds the workflow status and a task snapshot (id, status, required, assigned), then the counts and the `next_step`. It contains identifiers, statuses and counts only (no prompts or agent output), so Step 4.2 can checkpoint it.
- **Nodes.** `count_tasks` counts what tasks wait for: to be assigned (READY and unassigned, REWORK_REQUIRED, ESCALATED), in flight, awaiting verification, blocked or pending. A conditional edge (`route`, first match wins) then leads to exactly one terminal node. Each terminal node returns a `NextStep {kind, actor, summary, task_ids ≤ 50}`:

| Node | Kinds | Actor |
|---|---|---|
| `finished` | `finished` | none |
| `workflow_step` | `start_planning`, `complete_plan`, `approve_plan`, `start_workflow`, `resume_workflow` | people |
| `assign_agents` | `assign_agents` | people |
| `running` | `running` | agents |
| `awaiting_verification` | `awaiting_verification` (Phase 5) | platform |
| `blocked` | `blocked` | people |

- **Read-only.** The graph is pure: nodes only read and decide. Later nodes that act (verification, retries) must change state through the engine under the workflow lock (ADR-0009).

### Trigger and job
- **The trigger.** `engine.request_orchestration` writes a `workflow.orchestrate` outbox row (identifiers only) in every transaction that changes a workflow or task status, once per transaction. The deduplication marker is tied to the session's current transaction, because rows flushed earlier leave `session.new`. Creating a workflow changes no status and requests nothing.
- **The job.** `app/orchestration/job.py` runs in the Celery task `agentflow.orchestrate_workflow`, in one transaction:
  1. records the delivery in the inbox, so a redelivery is a no-op;
  2. locks the workflow row (workspace-scoped, so a delivery naming another workspace's workflow assesses nothing);
  3. snapshots the active tasks and runs the graph;
  4. stores `workflows.next_step` (the step plus `assessed_at` and `graph_version`).
- **Ordering.** Every run reads the committed state under the lock, so the delivery order doesn't matter.
- **Revision.** The assessment never bumps `revision`: it changes no plan, task or status, so it never causes edit conflicts.

### Storage and API
- **Storage.** Migration `0015_orchestration_next_step` adds `workflows.next_step` (JSONB, nullable). Workers already hold UPDATE on workflows.
- **API.** `WorkflowResponse.next_step` is null until the first assessment.

### UI
- **Banner.** The workflow page shows a "Next step" banner with a title by actor ("Next step for you", "Agents are working", "Waiting for the platform"), the summary and how recently it was assessed. It is hidden before the first assessment and for finished workflows.
- **Refresh.** Writing an assessment adds no event, so after new events the next five polls also refresh the workflow while its assessment is older than the latest event.

## Consequences
- **Phase 4 builds on this graph:**
  - checkpoints in PostgreSQL (4.2);
  - dispatch hardening (4.3);
  - dependency-aware scheduling (4.4);
  - recovery (4.5).
- **Phase 5 adds acting nodes:** verification, rework and replanning.
- **Volume.** Every status-changing transaction adds one outbox row and one short job. Assessment uses no model and no external call.
- **Tests.** The relay tests remove orchestration requests from their setup, as they already did for analysis requests; the orchestration tests cover them.
