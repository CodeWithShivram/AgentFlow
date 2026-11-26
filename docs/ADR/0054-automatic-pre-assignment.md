# ADR-0054: Automatic pre-assignment of software project tasks

- Status: Accepted
- Date: 2026-11-26
- Implements: Step 9.8; playbook §1 ("the user should not need to manually create … agent assignments") and §2C (delegate)
- Amends: ADR-0023 ("people decide; the platform only suggests") for software projects only
- Builds on: ADR-0023 (rule-based selection), ADR-0028 (pre-assignment), ADR-0032 (automatic reassignment)

## Context
- **The problem.** The end-to-end journey (9.8) showed that a software project could not run without someone pre-assigning an agent to each task in the advanced view.
- **What the product promises** is one prompt, an approved plan, and then the platform delegating the work.
- **What already exists.** Agent selection is a pure, versioned ranking whose rationale is stored server-side. Automatic reassignment (ADR-0032) already lets the platform pick within those rules.

## Decision
- **When a software project's plan is confirmed** (including a replan), the platform pre-assigns each new task to the **top-ranked eligible** agent:
  - the ranking is `selection/v1`, as for suggestions;
  - the agent's active published version is pinned;
  - the stored `agent_selections` row has no deciding person (`decided_by_user_id` NULL);
  - a `TASK_ASSIGNED` event is written with `actor: SCHEDULER`, `automatic: true` and `pre_assigned: true`.
- **Tasks stay PENDING.** The scheduler assigns and dispatches them when their dependencies are done (ADR-0028).
- **No eligible agent?** The task stays unassigned, with a platform proposal if the workflow allows proposals (ADR-0024).
- **People keep control.** They approved the plan, the next step for approval-required workflows. Before or after starting, they may change any assignment in the workflow controls, and that choice is recorded as theirs.
- **General workflows** keep ADR-0023 unchanged: the platform only suggests.

## Consequences
- **No manual assignment needed.** The journey from prompt to download completes without anyone assigning an agent.
- **Assignments stay auditable** like every other: the same rules version, factors and rank.
