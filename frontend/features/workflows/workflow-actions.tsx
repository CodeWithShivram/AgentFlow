"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { Workflow } from "@/lib/api/types";
import { useWorkspace } from "@/lib/workspace";

import { type ActionSpec, workflowActions } from "./status";
import { workflowKeys } from "./use-workflow";

interface WorkflowActionsProps {
  workflow: Workflow;
  onError(error: unknown): void;
}

export function WorkflowActions({ workflow, onError }: WorkflowActionsProps) {
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState<ActionSpec | null>(null);
  const actions = workflowActions(workflow.status, workflow.autonomy_policy, workspace.role);

  const run = useMutation({
    mutationFn: (spec: ActionSpec) => {
      const { id } = workflow;
      if (spec.action === "start") return api.workflows.start(workspace.id, id, workflow.revision);
      if (spec.action === "cancel") return api.workflows.cancel(workspace.id, id, workflow.revision);
      return api.workflows.transition(workspace.id, id, {
        to_status: spec.target,
        revision: workflow.revision,
      });
    },
    onSuccess: (updated, spec) => {
      queryClient.setQueryData(workflowKeys.detail(workspace.id, workflow.id), updated);
      void queryClient.invalidateQueries({ queryKey: workflowKeys.detail(workspace.id, workflow.id) });
      void queryClient.invalidateQueries({ queryKey: ["workflows", workspace.id, "list"] });
      void queryClient.invalidateQueries({ queryKey: ["workflows", workspace.id, "count"] });
      toast.success(`${spec.label}: done`);
      setConfirming(null);
    },
    onError: (error) => {
      setConfirming(null);
      onError(error);
    },
  });

  if (actions.length === 0) return null;

  return (
    <>
      {actions.map((spec) => (
        <Button
          key={spec.action}
          variant={spec.destructive ? "destructive" : spec === actions[0] ? "default" : "outline"}
          disabled={run.isPending}
          onClick={() => (spec.confirm ? setConfirming(spec) : run.mutate(spec))}
        >
          {run.isPending && run.variables === spec ? "Working…" : spec.label}
        </Button>
      ))}
      <ConfirmDialog
        open={confirming !== null}
        onOpenChange={(open) => !open && setConfirming(null)}
        title={confirming ? `${confirming.label}?` : ""}
        description={confirming?.confirm ?? ""}
        confirmLabel={confirming?.label ?? "Confirm"}
        destructive={confirming?.destructive}
        pending={run.isPending}
        onConfirm={() => confirming && run.mutate(confirming)}
      />
    </>
  );
}
