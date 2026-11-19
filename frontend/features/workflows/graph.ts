import type { Task, Uuid } from "@/lib/api/types";

/** What the graph needs of a task; workflow tasks and report tasks both fit. */
export type GraphTask = Pick<Task, "id" | "title" | "depends_on" | "priority" | "required" | "removed" | "status">;

/**
 * Groups tasks into stages: a task's stage is one more than the deepest task it depends on.
 * The API rejects cycles, but a defensive guard keeps the UI finite whatever it receives.
 */
export function dependencyStages<T extends GraphTask>(tasks: T[]): T[][] {
  const byId = new Map<Uuid, T>(tasks.map((t) => [t.id, t]));
  const depth = new Map<Uuid, number>();
  const visiting = new Set<Uuid>();

  const resolve = (task: T): number => {
    const known = depth.get(task.id);
    if (known !== undefined) return known;
    if (visiting.has(task.id)) return 0;
    visiting.add(task.id);
    let level = 0;
    for (const id of task.depends_on) {
      const dependency = byId.get(id);
      if (dependency) level = Math.max(level, resolve(dependency) + 1);
    }
    visiting.delete(task.id);
    depth.set(task.id, level);
    return level;
  };

  const stages: T[][] = [];
  for (const task of tasks) {
    const level = resolve(task);
    (stages[level] ??= []).push(task);
  }
  return stages
    .filter((stage) => stage !== undefined)
    .map((stage) => [...stage].sort((a, b) => b.priority - a.priority || a.title.localeCompare(b.title)));
}

/** Tasks that depend on `taskId`. */
export function dependents(tasks: Task[], taskId: Uuid): Task[] {
  return tasks.filter((t) => t.depends_on.includes(taskId));
}

// --- DAG visualization (ADR-0044) ------------------------------------------------------------

/** Every task the given one waits on, directly or through others. */
export function upstream(tasks: GraphTask[], taskId: Uuid): Set<Uuid> {
  const byId = new Map<Uuid, GraphTask>(tasks.map((t) => [t.id, t]));
  const seen = new Set<Uuid>();
  const visit = (id: Uuid) => {
    for (const dep of byId.get(id)?.depends_on ?? []) {
      if (!seen.has(dep) && byId.has(dep)) {
        seen.add(dep);
        visit(dep);
      }
    }
  };
  visit(taskId);
  seen.delete(taskId);
  return seen;
}

/** Every task that waits on the given one, directly or through others. */
export function downstream(tasks: GraphTask[], taskId: Uuid): Set<Uuid> {
  const seen = new Set<Uuid>();
  const visit = (id: Uuid) => {
    for (const t of tasks) {
      if (t.depends_on.includes(id) && !seen.has(t.id)) {
        seen.add(t.id);
        visit(t.id);
      }
    }
  };
  visit(taskId);
  seen.delete(taskId);
  return seen;
}

const FINISHED = new Set(["COMPLETED", "CANCELLED"]);

function byPriorityThenTitle(a: GraphTask, b: GraphTask): number {
  return b.priority - a.priority || a.title.localeCompare(b.title);
}

/**
 * The longest chain of unfinished required tasks, first to last: what holds the workflow up.
 * Ties go to higher priority, then title. Empty when every required task is finished.
 */
export function criticalPath(tasks: GraphTask[]): Uuid[] {
  const open = new Map<Uuid, GraphTask>(
    tasks.filter((t) => !t.removed && t.required && !FINISHED.has(t.status)).map((t) => [t.id, t]),
  );
  const length = new Map<Uuid, number>();
  const visiting = new Set<Uuid>();
  const longest = (id: Uuid): number => {
    const known = length.get(id);
    if (known !== undefined) return known;
    if (visiting.has(id)) return 0; // the API rejects cycles; stay finite anyway
    visiting.add(id);
    let best = 0;
    for (const dep of open.get(id)?.depends_on ?? []) {
      if (open.has(dep)) best = Math.max(best, longest(dep));
    }
    visiting.delete(id);
    length.set(id, best + 1);
    return best + 1;
  };
  const pick = (candidates: GraphTask[]): GraphTask | undefined =>
    [...candidates].sort((a, b) => longest(b.id) - longest(a.id) || byPriorityThenTitle(a, b))[0];

  let current = pick([...open.values()]);
  const path: Uuid[] = [];
  while (current) {
    path.unshift(current.id);
    const deps = current.depends_on.map((id) => open.get(id)).filter((t): t is GraphTask => t !== undefined);
    const next = pick(deps.filter((t) => !path.includes(t.id)));
    current = next && longest(next.id) === longest(current.id) - 1 ? next : undefined;
  }
  return path;
}

export interface GraphNode<T extends GraphTask = Task> {
  task: T;
  stage: number;
  row: number;
}

export interface GraphLayout<T extends GraphTask = Task> {
  nodes: GraphNode<T>[];
  // Each edge runs from a dependency to the task that waits on it.
  edges: { from: Uuid; to: Uuid }[];
  stages: T[][];
}

/**
 * Stages from `dependencyStages`, then each stage after the first ordered by the average row of
 * its tasks' dependencies (one barycentre pass), which keeps most edges short and uncrossed.
 */
export function layoutGraph<T extends GraphTask>(tasks: T[]): GraphLayout<T> {
  const stages = dependencyStages(tasks);
  const row = new Map<Uuid, number>();
  stages.forEach((stage, index) => {
    if (index > 0) {
      const centre = (t: T) => {
        const rows = t.depends_on.map((id) => row.get(id)).filter((r): r is number => r !== undefined);
        return rows.length ? rows.reduce((a, b) => a + b, 0) / rows.length : Number.POSITIVE_INFINITY;
      };
      stage.sort((a, b) => centre(a) - centre(b));
    }
    stage.forEach((t, i) => row.set(t.id, i));
  });
  const ids = new Set(tasks.map((t) => t.id));
  return {
    stages,
    nodes: stages.flatMap((stage, index) => stage.map((task, i) => ({ task, stage: index, row: i }))),
    edges: tasks.flatMap((t) => t.depends_on.filter((id) => ids.has(id)).map((id) => ({ from: id, to: t.id }))),
  };
}
