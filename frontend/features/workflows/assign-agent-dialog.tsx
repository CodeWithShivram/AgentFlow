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
import type { AgentSuggestion, Task } from "@/lib/api/types";
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

/** Reasons shown under an eligible agent: what the rules actually scored. */
function scoredReasons(agent: AgentSuggestion): string[] {
  return agent.factors.filter((f) => f.outcome === "scored").map((f) => f.detail);
}

/** Why an agent was excluded: the hard filters it failed. */
function exclusionReasons(agent: AgentSuggestion): string[] {
  return agent.factors.filter((f) => f.outcome === "fail").map((f) => f.detail);
}

/**
 * Agents ranked by the server's selection rules (ADR-0023), best first, with their reasons.
 * Suggestions only: the member picks any eligible agent, and the server stores the rationale.
 */
export function AssignAgentDialog({ task, workflowId, onOpenChange, onAssigned, onError }: AssignAgentDialogProps) {
  const workspace = useWorkspace();
  const [agentId, setAgentId] = useState<string | null>(null);
  const suggestions = useQuery({
    queryKey: ["workflows", workspace.id, workflowId, "tasks", task?.id, "agent-suggestions"],
    queryFn: () => api.tasks.agentSuggestions(workspace.id, workflowId, task!.id),
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
  const items = suggestions.data?.items ?? [];
  const eligible = items.filter((a) => a.eligible);
  const excluded = items.filter((a) => !a.eligible);

  return (
    <Dialog open={task !== null} onOpenChange={close}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Assign an agent</DialogTitle>
          <DialogDescription>
            Agents are ranked by skill fit, usable tools and current workload; you can pick any suitable
            one. The task pins the agent&rsquo;s current published version.
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
        {suggestions.isPending ? (
          <p className="text-sm text-muted-foreground">Ranking active agents…</p>
        ) : suggestions.isError ? (
          <p className="text-sm text-destructive">{describeError(suggestions.error)}</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No active agents. Activate an agent first.</p>
        ) : (
          <>
            {eligible.length === 0 ? (
              <p className="text-sm text-muted-foreground">No active agent is suitable for this task.</p>
            ) : (
              <div role="radiogroup" aria-label="Suitable agents" className="grid gap-2">
                {eligible.map((agent) => {
                  const selected = agentId === agent.agent_id;
                  return (
                    <button
                      key={agent.agent_id}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      onClick={() => setAgentId(agent.agent_id)}
                      className={cn(
                        "rounded-md border px-3 py-2 text-left text-sm transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                        selected && "border-primary bg-muted",
                      )}
                    >
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{agent.agent_name}</span>
                        <span className="text-xs text-muted-foreground">v{agent.agent_version}</span>
                        {agent.rank === 1 ? <Badge>Suggested</Badge> : null}
                        <span className="ml-auto text-xs text-muted-foreground">Score {agent.score}</span>
                      </span>
                      <span className="mt-1 block text-xs text-muted-foreground">{scoredReasons(agent).join(" ")}</span>
                    </button>
                  );
                })}
              </div>
            )}
            {excluded.length > 0 ? (
              <section aria-label="Not suitable" className="grid gap-1">
                <p className="text-xs font-medium text-muted-foreground">Not suitable</p>
                <ul className="grid gap-1 text-xs">
                  {excluded.map((agent) => (
                    <li key={agent.agent_id} className="rounded-md border border-dashed px-3 py-1.5">
                      <span className="font-medium">{agent.agent_name}</span>{" "}
                      <span className="text-amber-700 dark:text-amber-400">{exclusionReasons(agent).join(" ")}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </>
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
