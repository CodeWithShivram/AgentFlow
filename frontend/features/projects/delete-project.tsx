"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2Icon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { workflowKeys } from "@/features/workflows/use-workflow";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { Workflow, WorkflowStatus } from "@/lib/api/types";
import { useActionErrors } from "@/lib/hooks/use-action-errors";
import { workspacePath } from "@/lib/navigation";
import { useWorkspace } from "@/lib/workspace";

/** Statuses the API deletes directly (ADR-0058); any other status is cancelled first. */
export const DELETABLE_STATUSES: ReadonlySet<WorkflowStatus> = new Set(["PENDING", "COMPLETED", "FAILED", "CANCELLED"]);

/**
 * Deletes a project (admins only). A project still at work is cancelled first, in the same
 * confirmed action. Its files and downloads are removed; usage history stays in analytics.
 */
export function DeleteProjectAction({ workflow }: { workflow: Workflow }) {
  const workspace = useWorkspace();
  const router = useRouter();
  const queryClient = useQueryClient();
  const errors = useActionErrors();
  const [open, setOpen] = useState(false);
  const cancelFirst = !DELETABLE_STATUSES.has(workflow.status);

  const remove = useMutation({
    mutationFn: async () => {
      let revision = workflow.revision;
      if (cancelFirst) {
        const cancelled = await api.workflows.transition(workspace.id, workflow.id, {
          to_status: "CANCELLED",
          revision,
        });
        revision = cancelled.revision;
      }
      await api.workflows.remove(workspace.id, workflow.id, revision);
    },
    onSuccess: () => {
      setOpen(false);
      queryClient.removeQueries({ queryKey: workflowKeys.detail(workspace.id, workflow.id) });
      void queryClient.invalidateQueries({ queryKey: workflowKeys.all(workspace.id) });
      toast.success("Project deleted.");
      router.replace(workspacePath(workspace.id, "projects"));
    },
    onError: (error) => {
      setOpen(false);
      errors.handle(error);
    },
  });

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <Trash2Icon data-icon="inline-start" />
        {cancelFirst ? "Cancel & delete" : "Delete project"}
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={cancelFirst ? "Cancel and delete this project?" : "Delete this project?"}
        description={
          cancelFirst
            ? "The project is still in progress. AgentFlow cancels its work, then deletes it with its generated files and downloads. This cannot be undone."
            : "The project, its generated files and its downloads are deleted. Usage and cost history stay in analytics. This cannot be undone."
        }
        confirmLabel={cancelFirst ? "Cancel & delete" : "Delete project"}
        destructive
        pending={remove.isPending}
        onConfirm={() => remove.mutate()}
      />
    </>
  );
}
