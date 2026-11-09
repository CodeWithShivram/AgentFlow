import type { Task, Uuid } from "@/lib/api/types";

/**
 * Groups tasks into stages: a task's stage is one more than the deepest task it depends on.
 * The API rejects cycles, but a defensive guard keeps the UI finite whatever it receives.
 */
export function dependencyStages(tasks: Task[]): Task[][] {
  const byId = new Map<Uuid, Task>(tasks.map((t) => [t.id, t]));
  const depth = new Map<Uuid, number>();
  const visiting = new Set<Uuid>();

  const resolve = (task: Task): number => {
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

  const stages: Task[][] = [];
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
