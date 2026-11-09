"use client";

import { ChevronDownIcon, MoreHorizontalIcon } from "lucide-react";
import { Fragment, useState } from "react";

import { StatusBadge } from "@/components/common/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Agent, Task, Workflow } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { ExecutionsPanel } from "./executions-panel";
import { PLAN_EDITABLE, TASK_ASSIGNABLE, TASK_TERMINAL, WORKFLOW_EXECUTING } from "./status";

export type TaskAction = "edit" | "dependencies" | "assign" | "cancel" | "remove";

interface TaskListProps {
  workflow: Workflow;
  tasks: Task[];
  agentsById: Map<string, Agent>;
  canEdit: boolean;
  onAction(action: TaskAction, task: Task): void;
}

export function availableTaskActions(workflow: Workflow, task: Task): TaskAction[] {
  if (task.removed) return [];
  const actions: TaskAction[] = [];
  const planEditable = PLAN_EDITABLE.has(workflow.status);
  if (planEditable) actions.push("edit", "dependencies");
  if (WORKFLOW_EXECUTING.has(workflow.status) && TASK_ASSIGNABLE.has(task.status)) actions.push("assign");
  if (WORKFLOW_EXECUTING.has(workflow.status) && !TASK_TERMINAL.has(task.status)) actions.push("cancel");
  if (planEditable) actions.push("remove");
  return actions;
}

const DESTRUCTIVE: ReadonlySet<TaskAction> = new Set(["cancel", "remove"]);

const ACTION_LABELS: Record<TaskAction, string> = {
  edit: "Edit task",
  dependencies: "Dependencies…",
  assign: "Assign agent…",
  cancel: "Cancel task",
  remove: "Remove from plan",
};

export function TaskList({ workflow, tasks, agentsById, canEdit, onAction }: TaskListProps) {
  const [showRemoved, setShowRemoved] = useState(false);
  const titles = new Map(tasks.map((t) => [t.id, t.title]));
  const removedCount = tasks.filter((t) => t.removed).length;
  const visible = tasks
    .filter((t) => showRemoved || !t.removed)
    .sort((a, b) => Number(a.removed) - Number(b.removed) || b.priority - a.priority);

  return (
    <div className="space-y-3">
      <ul className="space-y-3" aria-label="Tasks">
        {visible.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            titles={titles}
            agentsById={agentsById}
            actions={canEdit ? availableTaskActions(workflow, task) : []}
            onAction={onAction}
          />
        ))}
      </ul>
      {removedCount > 0 ? (
        <Button variant="link" className="px-0" onClick={() => setShowRemoved(!showRemoved)}>
          {showRemoved ? "Hide removed tasks" : `Show ${removedCount} removed task${removedCount === 1 ? "" : "s"}`}
        </Button>
      ) : null}
    </div>
  );
}

function TaskCard({
  task,
  titles,
  agentsById,
  actions,
  onAction,
}: {
  task: Task;
  titles: Map<string, string>;
  agentsById: Map<string, Agent>;
  actions: TaskAction[];
  onAction(action: TaskAction, task: Task): void;
}) {
  const [expanded, setExpanded] = useState(false);
  const agent = task.assigned_agent_id ? agentsById.get(task.assigned_agent_id) : undefined;
  const panelId = `task-${task.id}-details`;
  const primary = actions.includes("assign") ? "assign" : null;

  return (
    <li className={cn("rounded-lg border bg-card", task.removed && "opacity-60")}>
      <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start">
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-medium break-words">{task.title}</h3>
            <StatusBadge status={task.status} />
            {task.required ? (
              <Badge variant="outline">Required</Badge>
            ) : (
              <Badge variant="outline" className="border-dashed text-muted-foreground">
                Optional
              </Badge>
            )}
            {task.removed ? <Badge variant="secondary">Removed</Badge> : null}
          </div>
          {task.description ? (
            <p className="line-clamp-2 text-sm whitespace-pre-wrap text-muted-foreground">{task.description}</p>
          ) : null}
          <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <div>
              <dt className="inline">Priority: </dt>
              <dd className="inline">{task.priority}</dd>
            </div>
            <div>
              <dt className="inline">Agent: </dt>
              <dd className="inline">
                {task.assigned_agent_id ? (agent?.name ?? "Assigned agent") : "Not assigned"}
              </dd>
            </div>
            {task.attempt_count > 0 ? (
              <div>
                <dt className="inline">Attempts: </dt>
                <dd className="inline">{task.attempt_count}</dd>
              </div>
            ) : null}
            {task.depends_on.length > 0 ? (
              <div>
                <dt className="inline">After: </dt>
                <dd className="inline">{task.depends_on.map((id) => titles.get(id) ?? "removed task").join(", ")}</dd>
              </div>
            ) : null}
          </dl>
          {task.required_skills.length > 0 ? (
            <ul className="flex flex-wrap gap-1" aria-label="Required skills">
              {task.required_skills.map((skill) => (
                <li key={skill}>
                  <Badge variant="secondary">{humanize(skill)}</Badge>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {primary ? (
            <Button size="sm" onClick={() => onAction("assign", task)}>
              Assign agent
            </Button>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            aria-expanded={expanded}
            aria-controls={panelId}
            onClick={() => setExpanded(!expanded)}
          >
            Details
            <ChevronDownIcon data-icon="inline-end" className={cn("transition-transform", expanded && "rotate-180")} />
          </Button>
          {actions.length > 0 ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${task.title}`}>
                  <MoreHorizontalIcon />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {actions.map((action, index) => (
                  <Fragment key={action}>
                    {DESTRUCTIVE.has(action) && index > 0 && !DESTRUCTIVE.has(actions[index - 1]) ? (
                      <DropdownMenuSeparator />
                    ) : null}
                    <DropdownMenuItem
                      variant={DESTRUCTIVE.has(action) ? "destructive" : "default"}
                      onSelect={() => onAction(action, task)}
                    >
                      {ACTION_LABELS[action]}
                    </DropdownMenuItem>
                  </Fragment>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}
        </div>
      </div>
      {expanded ? (
        <div id={panelId} className="space-y-4 border-t px-4 py-3">
          {task.acceptance_criteria.length > 0 ? (
            <section>
              <h4 className="mb-1 text-sm font-medium">Acceptance criteria</h4>
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {task.acceptance_criteria.map((c) => (
                  <li key={c.id}>{c.description}</li>
                ))}
              </ul>
            </section>
          ) : null}
          <section>
            <h4 className="mb-2 text-sm font-medium">Execution attempts</h4>
            <ExecutionsPanel task={task} agentsById={agentsById} />
          </section>
        </div>
      ) : null}
    </li>
  );
}
