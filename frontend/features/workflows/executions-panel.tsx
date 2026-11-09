"use client";

import { useQuery } from "@tanstack/react-query";

import { RelativeTime } from "@/components/common/relative-time";
import { StatusBadge } from "@/components/common/status-badge";
import { api } from "@/lib/api";
import { describeError } from "@/lib/api/errors";
import type { Agent, Task } from "@/lib/api/types";
import { useWorkspace } from "@/lib/workspace";

import { TASK_IN_FLIGHT } from "./status";
import { workflowKeys } from "./use-workflow";

interface ExecutionsPanelProps {
  task: Task;
  agentsById: Map<string, Agent>;
}

/** Every attempt of a task with the exact agent version it ran. Refreshed by the event poll. */
export function ExecutionsPanel({ task, agentsById }: ExecutionsPanelProps) {
  const workspace = useWorkspace();
  const executions = useQuery({
    queryKey: workflowKeys.executions(workspace.id, task.workflow_id, task.id),
    queryFn: () => api.tasks.executions(workspace.id, task.workflow_id, task.id),
    // Belt and braces next to the event poll: in-flight attempts also refresh on their own.
    refetchInterval: TASK_IN_FLIGHT.has(task.status) ? 5_000 : false,
  });

  if (executions.isPending) return <p className="text-sm text-muted-foreground">Loading attempts…</p>;
  if (executions.isError) return <p className="text-sm text-destructive">{describeError(executions.error)}</p>;
  if (executions.data.items.length === 0) {
    return <p className="text-sm text-muted-foreground">No execution attempts yet.</p>;
  }
  return (
    <ol className="space-y-2" aria-label={`Execution attempts for ${task.title}`}>
      {executions.data.items.map((e) => (
        <li key={e.id} className="rounded-md border p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">Attempt {e.attempt_number}</span>
            <StatusBadge status={e.status} />
            <span className="text-muted-foreground">
              {agentsById.get(e.agent_id)?.name ?? "Agent"} · version {e.agent_version}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Started <RelativeTime value={e.started_at} />
            {e.completed_at ? (
              <>
                {" "}
                · finished <RelativeTime value={e.completed_at} />
              </>
            ) : null}
          </p>
          {e.result_summary ? <p className="mt-2 whitespace-pre-wrap break-words">{e.result_summary}</p> : null}
          {e.error_summary ? (
            <p className="mt-2 text-destructive">
              {e.error_code ? <span className="font-mono text-xs">{e.error_code}: </span> : null}
              {e.error_summary}
            </p>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
