"use client";

import { useQuery } from "@tanstack/react-query";

import { RelativeTime } from "@/components/common/relative-time";
import { StatusBadge } from "@/components/common/status-badge";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { describeError } from "@/lib/api/errors";
import type { AcceptanceCriterion, Agent, AgentSubmission, CriterionStatus, Task } from "@/lib/api/types";
import { useWorkspace } from "@/lib/workspace";

import { TASK_IN_FLIGHT } from "./status";
import { workflowKeys } from "./use-workflow";

interface ExecutionsPanelProps {
  task: Task;
  agentsById: Map<string, Agent>;
}

const CLAIM_LABELS: Record<CriterionStatus, string> = {
  MET: "Met",
  PARTIAL: "Partly met",
  NOT_MET: "Not met",
};

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
          {e.submission ? <SubmissionDetails submission={e.submission} criteria={task.acceptance_criteria} /> : null}
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

/**
 * The structured submission of a successful attempt (Step 3.1). Agent output is untrusted and
 * rendered as plain text; criterion claims are the agent's own until verification checks them.
 */
function SubmissionDetails({ submission, criteria }: { submission: AgentSubmission; criteria: AcceptanceCriterion[] }) {
  const descriptions = new Map(criteria.map((c) => [c.id, c.description]));
  return (
    <div className="mt-2 space-y-2">
      {submission.criteria.length > 0 ? (
        <section aria-label="Agent's criterion claims">
          <p className="text-xs font-medium text-muted-foreground">Agent&apos;s claims (not verified yet)</p>
          <ul className="mt-1 space-y-1">
            {submission.criteria.map((claim) => (
              <li key={claim.criterion_id} className="break-words">
                <Badge variant="outline" className="mr-2">
                  {CLAIM_LABELS[claim.status]}
                </Badge>
                {descriptions.get(claim.criterion_id) ?? claim.criterion_id}
                <span className="block text-xs text-muted-foreground">{claim.evidence}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <Notes title="Open questions" items={submission.open_questions} />
      <Notes title="Assumptions" items={submission.assumptions} />
      <Notes title="Limitations" items={submission.limitations} />
    </div>
  );
}

function Notes({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <section aria-label={title}>
      <p className="text-xs font-medium text-muted-foreground">{title}</p>
      <ul className="mt-1 list-disc space-y-1 pl-5">
        {items.map((item, i) => (
          <li key={i} className="break-words">
            {item}
          </li>
        ))}
      </ul>
    </section>
  );
}
