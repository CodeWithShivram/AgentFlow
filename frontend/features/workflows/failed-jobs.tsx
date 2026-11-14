"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { TriangleAlertIcon } from "lucide-react";
import { toast } from "sonner";

import { RelativeTime } from "@/components/common/relative-time";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { describeError } from "@/lib/api/errors";
import type { FailedJob, Task } from "@/lib/api/types";
import { useCan, useWorkspace } from "@/lib/workspace";

import { workflowKeys } from "./use-workflow";

const JOB_LABELS: Record<string, string> = {
  "task.dispatch": "Run task",
  "workflow.analyze": "Analyse request",
  "workflow.plan": "Draft plan",
  "workflow.orchestrate": "Assess next step",
};

export const failedJobsKey = (workspaceId: string, workflowId: string) =>
  [...workflowKeys.detail(workspaceId, workflowId), "failed-jobs"] as const;

/**
 * Background jobs of this workflow that the platform gave up on (Step 4.3, ADR-0027). Everyone
 * sees them; admins can retry one, which requeues it safely (consumers ignore duplicates).
 */
export function FailedJobs({ workflowId, tasks }: { workflowId: string; tasks: Task[] }) {
  const workspace = useWorkspace();
  const isAdmin = useCan("ADMIN");
  const queryClient = useQueryClient();
  const key = failedJobsKey(workspace.id, workflowId);
  const jobs = useQuery({ queryKey: key, queryFn: () => api.workflows.failedJobs(workspace.id, workflowId) });
  const retry = useMutation({
    mutationFn: (job: FailedJob) => api.workflows.retryFailedJob(workspace.id, workflowId, job.id),
    onSuccess: () => {
      toast.success("Job queued again");
      void queryClient.invalidateQueries({ queryKey: workflowKeys.detail(workspace.id, workflowId) });
    },
    onError: (error) => toast.error(describeError(error)),
  });

  const items = jobs.data?.items ?? [];
  if (items.length === 0) return null;
  const titles = new Map(tasks.map((t) => [t.id, t.title]));

  return (
    <Alert variant="destructive" role="region" aria-label="Failed background jobs">
      <TriangleAlertIcon aria-hidden />
      <AlertTitle>
        {items.length} background {items.length === 1 ? "job" : "jobs"} failed
      </AlertTitle>
      <AlertDescription>
        <ul className="mt-1 w-full space-y-2">
          {items.map((job) => (
            <li key={job.id} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium">{JOB_LABELS[job.event_type] ?? job.event_type}</span>
              {job.task_id ? <span>· {titles.get(job.task_id) ?? "a removed task"}</span> : null}
              <span className="text-xs text-muted-foreground">
                {job.attempts} {job.attempts === 1 ? "attempt" : "attempts"}
                {job.error_type ? ` · ${job.error_type}` : ""} · failed <RelativeTime value={job.failed_at} />
              </span>
              {isAdmin ? (
                <Button
                  size="sm"
                  variant="outline"
                  className="ml-auto"
                  disabled={retry.isPending}
                  onClick={() => retry.mutate(job)}
                >
                  Retry
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
        {!isAdmin ? <p className="text-xs">An admin can retry failed jobs.</p> : null}
      </AlertDescription>
    </Alert>
  );
}
