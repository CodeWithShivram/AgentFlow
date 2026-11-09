"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { FormError } from "@/components/common/form-field";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import type { Agent, Task } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useWorkspace } from "@/lib/workspace";

interface AssignAgentDialogProps {
  task: Task | null;
  workflowId: string;
  onOpenChange(open: boolean): void;
  onAssigned(): void;
  onError(error: unknown): void;
}

/** A hint only: the list does not include the published configuration, so the draft is used
 *  when it matches the published version. The API checks the pinned version on assignment. */
function missingSkills(agent: Agent, task: Task): string[] {
  if (agent.has_unpublished_changes) return [];
  const skills = new Set(agent.draft_configuration?.capabilities ?? []);
  return task.required_skills.filter((s) => !skills.has(s));
}

export function AssignAgentDialog({ task, workflowId, onOpenChange, onAssigned, onError }: AssignAgentDialogProps) {
  const workspace = useWorkspace();
  const [agentId, setAgentId] = useState<string | null>(null);
  const agents = useQuery({
    queryKey: ["agents", workspace.id, "list", { status: "ACTIVE", limit: 100, offset: 0 }],
    queryFn: () => api.agents.list(workspace.id, { status: "ACTIVE", limit: 100 }),
    enabled: task !== null,
  });

  const assign = useMutation({
    mutationFn: (id: string) => api.tasks.assign(workspace.id, workflowId, task!.id, id, task!.revision),
    onSuccess: () => {
      toast.success("Agent assigned. The task is queued for execution.");
      setAgentId(null);
      onAssigned();
      onOpenChange(false);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.isRevisionConflict) {
        onOpenChange(false);
        onError(error);
      }
    },
  });

  const close = (open: boolean) => {
    if (assign.isPending) return;
    assign.reset();
    setAgentId(null);
    onOpenChange(open);
  };

  const missingFromError =
    assign.error instanceof ApiError && Array.isArray(assign.error.details.missing_skills)
      ? (assign.error.details.missing_skills as unknown[]).map(String)
      : [];

  return (
    <Dialog open={task !== null} onOpenChange={close}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Assign an agent</DialogTitle>
          <DialogDescription>
            The task pins the agent&rsquo;s current published version, so later edits to the agent do
            not change this run.
          </DialogDescription>
        </DialogHeader>
        <FormError
          message={
            assign.isError && !(assign.error instanceof ApiError && assign.error.isRevisionConflict)
              ? missingFromError.length > 0
                ? `The agent lacks: ${missingFromError.map(humanize).join(", ")}.`
                : describeError(assign.error)
              : null
          }
        />
        {agents.isPending ? (
          <p className="text-sm text-muted-foreground">Loading active agents…</p>
        ) : agents.isError ? (
          <p className="text-sm text-destructive">{describeError(agents.error)}</p>
        ) : agents.data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No active agents. Activate an agent first.</p>
        ) : (
          <div role="radiogroup" aria-label="Active agents" className="grid gap-2">
            {agents.data.items.map((agent) => {
              const missing = task ? missingSkills(agent, task) : [];
              const selected = agentId === agent.id;
              return (
                <button
                  key={agent.id}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  onClick={() => setAgentId(agent.id)}
                  className={cn(
                    "rounded-md border px-3 py-2 text-left text-sm transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                    selected && "border-primary bg-muted",
                  )}
                >
                  <span className="flex items-center gap-2">
                    <span className="font-medium">{agent.name}</span>
                    <span className="text-xs text-muted-foreground">v{agent.active_version}</span>
                    {agent.is_builtin ? <Badge variant="outline">Built-in</Badge> : null}
                  </span>
                  {missing.length > 0 ? (
                    <span className="mt-1 block text-xs text-amber-700 dark:text-amber-400">
                      Lacks: {missing.map(humanize).join(", ")}
                    </span>
                  ) : (
                    <span className="mt-1 block text-xs text-muted-foreground">{humanize(agent.category)}</span>
                  )}
                </button>
              );
            })}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => close(false)} disabled={assign.isPending}>
            Cancel
          </Button>
          <Button disabled={!agentId || assign.isPending} onClick={() => agentId && assign.mutate(agentId)}>
            {assign.isPending ? "Assigning…" : "Assign"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
