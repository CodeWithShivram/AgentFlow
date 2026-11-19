# ADR-0044: Workflow DAG visualization

- Status: Accepted
- Date: 2026-11-19
- Implements: Phase 8, Step 8.1 (workflow DAG visualization). Handoff references:
  - §14 screen 3 (Workflow Detail: "Task DAG", task progress);
  - §14 screen 4 (Task Detail: assigned agent, execution attempts), reached from the graph.
- Builds on: ADR-0011 (web application), ADR-0016 (DAG checks and the stage preview), ADR-0028 (priorities), ADR-0034 (required tasks and acceptance).
- Owner decisions (2026-11-19):
  - a hand-built SVG on the existing stage layout, with no new dependency;
  - selecting a task highlights its whole upstream and downstream chain and opens a panel with its details and attempts;
  - a "Show critical path" toggle;
  - it replaces the workflow's Dependencies tab only; the plan-draft preview keeps its stage list.

## Context
- **Dependencies were shown only as lists.** The Dependencies tab grouped tasks into stages, each card with an "After: …" line. People could not see at a glance what a task waited on further back, what it held up, or which chain decided when the workflow could finish.
- **The data is already on the page.** The workflow view loads every task with `depends_on`, status, priority, `required` and `attempt_count`, and the workspace's agents. Attempts load per task through the existing executions endpoint.

## Decisions

### Layout (`features/workflows/graph.ts`)
- **Stages stay the columns.** `dependencyStages` (unchanged) puts a task one stage after its deepest dependency, so every arrow points right.
- **One barycentre pass orders the rows.** `layoutGraph` orders each later stage by the average row of its tasks' dependencies, which keeps most edges short and uncrossed. Equal positions keep the existing priority-then-title order. Edges run from a dependency to the task that waits on it.
- **Fixed geometry.** Columns are 240 px wide with 64 px gaps and cards are 136 px tall, so the SVG edges are computed without measuring the DOM. Wide graphs scroll horizontally.
- **No new dependency.** A graph library (React Flow, dagre, ELK) would add weight for a layout that is already fixed by stages, at the plan size limit of 25 tasks per draft.

### Reading the graph
- **Cards** show the title, status, "Optional" for optional tasks, the agent (or "Unassigned"), the attempt count, a non-zero priority and the "After: …" line. The arrows are decorative (`aria-hidden`); the "After:" text and the details panel carry the same facts for screen readers.
- **Selection.** Clicking a card (`aria-pressed`) highlights it, everything it waits on (`upstream`) and everything that waits on it (`downstream`), transitively, and fades the rest. Clicking it again or "Close" clears the selection.
- **Details panel.** It shows status, required, priority, agent, skills, the direct and transitive dependencies, the transitive dependents, and the task's attempts through the existing `ExecutionsPanel`.
- **Critical path.** `criticalPath` is the longest chain of unfinished required tasks (not completed, cancelled or removed), first to last: what holds the workflow up. Ties go to higher priority, then title. The toggle marks its cards and edges and states it as text ("Critical path: A → B"), or "Every required task is finished."
- **Defensive.** The API rejects cycles; every traversal still guards against one so the page stays finite whatever it receives.

### Scope
- **Read-only.** The graph changes nothing. Dependencies are still edited from the Tasks tab, through the existing API.
- **The plan-draft preview is unchanged.** It keeps its stage list and validation (ADR-0016).
- **No backend change.** No new endpoint, schema or migration.

## Consequences
- **The workflow's structure is visible:** chains, bottlenecks and the remaining critical path, with statuses refreshing as the workflow page polls.
- **The critical path counts tasks, not time.** Durations are not known before tasks run, so "longest" means the most unfinished tasks in a chain. A duration-weighted path could use ADR-0043's measurements later.
- **Very wide stages make tall graphs.** At the 25-task draft limit this stays readable. Larger plans would need zooming or collapsing, which is out of scope.
