"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import { toast } from "sonner";

import { RelativeTime } from "@/components/common/relative-time";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { describeError } from "@/lib/api/errors";
import type { Workflow } from "@/lib/api/types";
import { useCan, useWorkspace } from "@/lib/workspace";

import { PLAN_EDITABLE } from "./status";
import { workflowKeys } from "./use-workflow";

/**
 * Status of the generated plan draft (Step 2.3). Drafts are proposals: the plan itself changes
 * only when a draft is confirmed. The full draft preview arrives with Step 2.4.
 */
export function PlanDraftStatus({ workflow }: { workflow: Workflow }) {
  const workspace = useWorkspace();
  const canRegenerate = useCan("MEMBER") && PLAN_EDITABLE.has(workflow.status) && workflow.status !== "PENDING";
  const queryClient = useQueryClient();
  const drafts = useQuery({
    queryKey: workflowKeys.planDrafts(workspace.id, workflow.id),
    queryFn: () => api.workflows.planDrafts(workspace.id, workflow.id),
    enabled: workflow.plan_status !== "NOT_REQUESTED",
  });
  const regenerate = useMutation({
    mutationFn: () => api.workflows.regeneratePlan(workspace.id, workflow.id),
    onSuccess: (updated) => {
      queryClient.setQueryData(workflowKeys.detail(workspace.id, workflow.id), updated);
      void queryClient.invalidateQueries({ queryKey: workflowKeys.events(workspace.id, workflow.id) });
      toast.success("A new plan draft was requested");
    },
    onError: (error) => toast.error(describeError(error)),
  });

  const latest = drafts.data?.items[0];
  const regenerateButton = canRegenerate ? (
    <Button
      variant="outline"
      size="sm"
      disabled={regenerate.isPending}
      aria-busy={regenerate.isPending}
      onClick={() => regenerate.mutate()}
    >
      <RefreshCwIcon data-icon="inline-start" />
      {regenerate.isPending ? "Requesting…" : "Regenerate plan"}
    </Button>
  ) : null;

  if (workflow.plan_status === "NOT_REQUESTED") return null;
  if (workflow.plan_status === "PENDING") {
    return (
      <div role="status" className="flex items-center gap-3 rounded-lg border p-4 text-sm">
        <LoaderCircleIcon className="size-4 animate-spin" aria-hidden />
        Drafting a plan from the analysis…
      </div>
    );
  }
  if (workflow.plan_status === "FAILED") {
    return (
      <Alert variant="destructive">
        <AlertTitle>The plan draft failed</AlertTitle>
        <AlertDescription className="space-y-3">
          <p>The model did not return a valid plan after several attempts. You can still add tasks yourself.</p>
          {regenerateButton}
        </AlertDescription>
      </Alert>
    );
  }
  return (
    <Alert>
      <AlertTitle>
        {latest
          ? `Draft plan version ${latest.version} is ready with ${latest.task_count} ${latest.task_count === 1 ? "task" : "tasks"}`
          : "A draft plan is ready"}
      </AlertTitle>
      <AlertDescription className="space-y-3">
        <p>
          It is a proposal: your tasks do not change until you review and confirm a draft.
          {latest ? (
            <>
              {" "}
              Generated <RelativeTime value={latest.created_at} />.
            </>
          ) : null}
        </p>
        {regenerateButton}
      </AlertDescription>
    </Alert>
  );
}
