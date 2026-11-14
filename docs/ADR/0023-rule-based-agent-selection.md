# ADR-0023: Rule-based agent selection with persisted rationale

- Status: Accepted
- Date: 2026-11-14
- Implements: Phase 3, Step 3.6 (rule-based agent selection with persisted rationale); handoff §7 ("explainable rule-based scoring and persist the selection rationale"; no ML ranking)
- Builds on: ADR-0008 (registry), ADR-0019 (tools), ADR-0021/0022 (runtimes)
- Owner decisions (2026-11-14):
  - the platform suggests and people assign;
  - execution history is not scored until verification exists;
  - rationales go in a new append-only table;
  - the Assign dialog ranks agents with reasons.

## Decisions

### Rules (`app/agents/selection.py`, version `selection/v1`)
- **Pure functions.** Every ACTIVE agent visible to the workspace (its own and built-ins), with its active version, is a candidate. Each candidate gets one factor per rule: name, outcome (`pass`/`fail`/`scored`/`not_scored`), points and a plain reason.
- **Hard filters** exclude a candidate with score 0 and no rank:
  - **skills:** it lacks a required skill, and the reason lists which;
  - **runtime:** its instruction reference has no runtime, for example custom `workspace://` instructions.
- **Scored factors** (maximum 90):

| Factor | Maximum | How |
|---|---|---|
| `skill_fit` | 40 | Share of the agent's skills that the task requires, so specialists outrank generalists. 0 when the task lists no skills. |
| `tool_fit` | 20 | Share of the agent's granted tools that are usable now (ADR-0019). 0 for every agent today. |
| `workload` | 30 | 30 / (1 + its tasks in the workspace that are ASSIGNED, QUEUED or IN_PROGRESS). |

- **Reported as not scored, never guessed:**
  - `history`: until Phase 5 verification, because a successful attempt today only means its output was well-formed;
  - `cost`: until Phase 7 pricing;
  - `latency`: not measured;
  - `policy`: no workspace selection policy exists.
- **Order.** Eligible candidates come first, by score, then name, then id, and get ranks 1…n. Excluded candidates follow. Runnable references come from the instruction registry (tests keep it equal to the runtimes), so the API never loads the model gateway.
- **Versioning.** Changing weights or factors means a new rules version.

### Suggestions and assignment
- **Suggestions.** `GET …/tasks/{id}/agent-suggestions` (Viewer) returns the rules version and the ranked candidates with their factors. It is read-only.
- **Assignment** still takes the member's choice: any ACTIVE agent with the skills, as before. In the same transaction, the server:
  - recomputes the ranking;
  - inserts an `agent_selections` row for the chosen agent: eligible, score, rank, top suggestion, candidate count, rules version, factors, and the deciding member;
  - adds `selection_id` and `selection_rank` to the `TASK_ASSIGNED`/`TASK_REASSIGNED` event.

  The rationale is never taken from the client, and choosing someone other than the top suggestion is recorded through rank and `top_agent_id`.
- **History.** `GET …/tasks/{id}/agent-selections` (Viewer) lists a task's rationales, newest first.

### Storage (migration `0013_agent_selections`)
- **Keys.** Composite foreign keys tie each row to its workspace's workflow and task and to the exact agent version. A `RESTRICT` reference to the deciding user preserves history.
- **Constraints:**
  - the rank is set exactly when the candidate was eligible;
  - the rank is ≥ 1;
  - the candidate count is ≥ 1;
  - the score is ≥ 0.
- **Append-only.** A trigger rejects UPDATE. The table has tenant and platform RLS policies. Workers have no access, since they don't need it.

### UI
- **Assign dialog.** It lists suitable agents best first, with a "Suggested" badge on rank 1, the score and the scored reasons. "Not suitable" agents appear separately with the failed rules, and can't be selected.

## Consequences
- **Phase 5 history.** Quality history can join the score once verification produces trustworthy outcomes. That needs a new rules version.
- **Automatic assignment** (for example in autonomous workflows) can reuse the same ranking and table with `decided_by_user_id` NULL, but is not enabled: people decide.
- **Assignment rules unchanged.** Agents without a runtime can still be assigned, as in ADR-0021: the attempt fails clearly. The rationale records them as not eligible.
