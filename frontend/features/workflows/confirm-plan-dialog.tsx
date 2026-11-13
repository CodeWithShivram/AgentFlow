"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { FormError } from "@/components/common/form-field";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { ApiError, describeError } from "@/lib/api/errors";
import type { PlanDraftPreview, Workflow } from "@/lib/api/types";
import { useWorkspace } from "@/lib/workspace";

import { PLAN_EDIT_REOPENS } from "./status";
import { workflowKeys } from "./use-workflow";

type ExistingTasks = "replace" | "append";

interface ConfirmPlanDialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  workflow: Workflow;
  preview: PlanDraftPreview;
  existingTaskCount: number;
  onConfirmed(): void;
  onConflict(error: unknown): void;
}

/**
 * Confirms a plan draft into the workflow's tasks (Step 2.5). The user acknowledges warnings
 * and, when tasks exist, chooses whether the draft replaces them or is added alongside.
 */
export function ConfirmPlanDialog({
  open,
  onOpenChange,
  workflow,
  preview,
  existingTaskCount,
  onConfirmed,
  onConflict,
}: ConfirmPlanDialogProps) {
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const warnings = preview.issues.filter((i) => i.severity === "warning");
  const [acknowledged, setAcknowledged] = useState(false);
  const [existing, setExisting] = useState<ExistingTasks>("replace");

  const confirm = useMutation({
    mutationFn: () =>
      api.workflows.confirmPlanDraft(workspace.id, workflow.id, preview.version, {
        revision: workflow.revision,
        existing_tasks: existingTaskCount > 0 ? existing : undefined,
        acknowledge_warnings: warnings.length > 0 ? acknowledged : false,
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(workflowKeys.detail(workspace.id, workflow.id), updated);
      void queryClient.invalidateQueries({ queryKey: workflowKeys.detail(workspace.id, workflow.id) });
      toast.success(`Plan confirmed: ${preview.task_count} ${preview.task_count === 1 ? "task" : "tasks"} created`);
      onOpenChange(false);
      onConfirmed();
    },
    onError: (error) => {
      if (error instanceof ApiError && error.isRevisionConflict) {
        onOpenChange(false);
        onConflict(error);
      }
    },
  });

  const blocked = warnings.length > 0 && !acknowledged;
  const showError =
    confirm.isError && !(confirm.error instanceof ApiError && confirm.error.isRevisionConflict);

  return (
    <Dialog open={open} onOpenChange={(next) => !confirm.isPending && onOpenChange(next)}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Confirm draft version {preview.version}</DialogTitle>
          <DialogDescription>
            This creates {preview.task_count} {preview.task_count === 1 ? "task" : "tasks"} with their
            dependencies. Nothing runs until the plan is approved and started.
            {PLAN_EDIT_REOPENS.has(workflow.status)
              ? " The workflow goes back to planning, so the plan must be submitted again."
              : ""}
          </DialogDescription>
        </DialogHeader>
        <FormError message={showError ? describeError(confirm.error) : null} />

        {existingTaskCount > 0 ? (
          <fieldset className="grid gap-2">
            <legend className="mb-1 text-sm font-medium">
              The workflow already has {existingTaskCount} {existingTaskCount === 1 ? "task" : "tasks"}
            </legend>
            {(
              [
                ["replace", "Replace them", "They are removed from the plan and kept as history."],
                ["append", "Add the draft alongside", "Existing tasks stay; the draft's tasks are added."],
              ] as const
            ).map(([value, label, hint]) => (
              <label key={value} className="flex items-start gap-2 text-sm">
                <input
                  type="radio"
                  name="existing-tasks"
                  value={value}
                  checked={existing === value}
                  onChange={() => setExisting(value)}
                  className="mt-1"
                />
                <span>
                  <span className="font-medium">{label}</span>
                  <span className="block text-muted-foreground">{hint}</span>
                </span>
              </label>
            ))}
          </fieldset>
        ) : null}

        {warnings.length > 0 ? (
          <div className="grid gap-2 rounded-md border p-3 text-sm">
            <p className="font-medium">Warnings</p>
            <ul className="list-disc space-y-1 pl-5">
              {warnings.map((w, i) => (
                <li key={i}>{w.message}</li>
              ))}
            </ul>
            <label className="flex items-center gap-2">
              <Checkbox checked={acknowledged} onCheckedChange={(v) => setAcknowledged(v === true)} />
              I understand these warnings
            </label>
          </div>
        ) : null}

        <DialogFooter>
          <Button variant="outline" disabled={confirm.isPending} onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={blocked || confirm.isPending} aria-busy={confirm.isPending} onClick={() => confirm.mutate()}>
            {confirm.isPending ? "Confirming…" : "Confirm plan"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
