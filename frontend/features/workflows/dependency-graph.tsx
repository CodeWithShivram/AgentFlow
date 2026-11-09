"use client";

import { ArrowRightIcon } from "lucide-react";
import { useState } from "react";

import { StatusBadge } from "@/components/common/status-badge";
import type { Task } from "@/lib/api/types";
import { cn } from "@/lib/utils";

import { dependencyStages, dependents } from "./graph";

/**
 * Tasks laid out in stages (columns): every task sits to the right of everything it depends on.
 * Selecting a task highlights its dependencies and dependents. The same information is in the
 * text of each card, so it does not rely on colour or position.
 */
export function DependencyGraph({ tasks }: { tasks: Task[] }) {
  const visible = tasks.filter((t) => !t.removed);
  const [focused, setFocused] = useState<string | null>(null);
  const stages = dependencyStages(visible);
  const titles = new Map(visible.map((t) => [t.id, t.title]));
  const focusTask = visible.find((t) => t.id === focused);
  const related = new Set<string>(
    focusTask ? [...focusTask.depends_on, ...dependents(visible, focusTask.id).map((t) => t.id)] : [],
  );

  if (visible.length === 0) return <p className="text-sm text-muted-foreground">No tasks yet.</p>;

  return (
    <div className="overflow-x-auto pb-2">
      <ol className="flex min-w-max items-start gap-3" aria-label="Task stages">
        {stages.map((stage, index) => (
          <li key={index} className="flex items-start gap-3">
            <section className="w-60 space-y-2" aria-label={`Stage ${index + 1}`}>
              <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Stage {index + 1}
              </h3>
              <ul className="space-y-2">
                {stage.map((task) => (
                  <li key={task.id}>
                    <button
                      type="button"
                      aria-pressed={focused === task.id}
                      onClick={() => setFocused(focused === task.id ? null : task.id)}
                      className={cn(
                        "w-full rounded-md border bg-card p-3 text-left text-sm transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                        focused === task.id && "border-primary ring-1 ring-primary",
                        related.has(task.id) && "border-primary/60 bg-muted",
                        focused && focused !== task.id && !related.has(task.id) && "opacity-50",
                      )}
                    >
                      <span className="line-clamp-2 font-medium">{task.title}</span>
                      <span className="mt-2 flex flex-wrap items-center gap-1">
                        <StatusBadge status={task.status} />
                        {!task.required ? <span className="text-xs text-muted-foreground">Optional</span> : null}
                      </span>
                      {task.depends_on.length > 0 ? (
                        <span className="mt-2 block text-xs text-muted-foreground">
                          After: {task.depends_on.map((id) => titles.get(id) ?? "removed task").join(", ")}
                        </span>
                      ) : null}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
            {index < stages.length - 1 ? (
              <ArrowRightIcon className="mt-8 size-4 shrink-0 text-muted-foreground" aria-hidden />
            ) : null}
          </li>
        ))}
      </ol>
    </div>
  );
}
