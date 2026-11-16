"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
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
  // A person's optional note when pausing (ADR-0036), shown on the workflow while paused.
  const [note, setNote] = useState("");
  const close = () => {
    setConfirming(null);
    setNote("");
  };
  const actions = workflowActions(workflow.status, workflow.autonomy_policy, workspace.role);

  const run = useMutation({
    mutationFn: (spec: ActionSpec) => {
      const { id } = workflow;
      if (spec.action === "start") return api.workflows.start(workspace.id, id, workflow.revision);
      if (spec.action === "cancel") return api.workflows.cancel(workspace.id, id, workflow.revision);
      const reason = spec.target === "PAUSED" ? note.trim() : "";
      return api.workflows.transition(workspace.id, id, {
        to_status: spec.target,
        revision: workflow.revision,
        ...(reason ? { reason } : {}),
      });
    },
    onSuccess: (updated, spec) => {
      queryClient.setQueryData(workflowKeys.detail(workspace.id, workflow.id), updated);
      void queryClient.invalidateQueries({ queryKey: workflowKeys.detail(workspace.id, workflow.id) });
      void queryClient.invalidateQueries({ queryKey: ["workflows", workspace.id, "list"] });
      void queryClient.invalidateQueries({ queryKey: ["workflows", workspace.id, "count"] });
      toast.success(`${spec.label}: done`);
      close();
    },
    onError: (error) => {
      close();
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
        onOpenChange={(open) => !open && close()}
        title={confirming ? `${confirming.label}?` : ""}
        description={confirming?.confirm ?? ""}
        confirmLabel={confirming?.label ?? "Confirm"}
        destructive={confirming?.destructive}
        pending={run.isPending}
        onConfirm={() => confirming && run.mutate(confirming)}
      >
        {confirming?.target === "PAUSED" ? (
          <div className="grid gap-1.5">
            <Label htmlFor="pause-note">Reason (optional)</Label>
            <Textarea
              id="pause-note"
              rows={2}
              maxLength={500}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
        ) : null}
      </ConfirmDialog>
    </>
  );
}
