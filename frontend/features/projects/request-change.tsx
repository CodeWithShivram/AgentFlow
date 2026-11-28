"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { GitBranchPlusIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { workflowKeys } from "@/features/workflows/use-workflow";
import { api } from "@/lib/api";
import type { Workflow } from "@/lib/api/types";
import { useActionErrors } from "@/lib/hooks/use-action-errors";
import { useIdempotencyKey } from "@/lib/hooks/use-idempotency-key";
import { workspacePath } from "@/lib/navigation";
import { useWorkspace } from "@/lib/workspace";

/** The API's prompt limit (`MAX_PROMPT_LENGTH`). */
const MAX_PROMPT = 20_000;

/**
 * Asks for a change to a completed project (ADR-0059). AgentFlow starts a new project from this
 * project's verified code, plans only the change, runs every check again and packages a new ZIP.
 * This project and its downloads stay as they are.
 */
export function RequestChange({ workflow }: { workflow: Workflow }) {
  const workspace = useWorkspace();
  const router = useRouter();
  const queryClient = useQueryClient();
  const errors = useActionErrors();
  const keyFor = useIdempotencyKey();
  const fieldId = useId();
  const [prompt, setPrompt] = useState("");
  const [invalid, setInvalid] = useState(false);

  const create = useMutation({
    mutationFn: (body: { prompt: string }) =>
      api.workflows.createChange(workspace.id, workflow.id, body, keyFor(body)),
    onSuccess: (change) => {
      void queryClient.invalidateQueries({ queryKey: workflowKeys.all(workspace.id) });
      toast.success("Change project created. Plan it to start.");
      router.push(workspacePath(workspace.id, `projects/${change.id}`));
    },
    onError: (error) => errors.handle(error),
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!prompt.trim()) {
      setInvalid(true);
      return;
    }
    create.mutate({ prompt });
  }

  return (
    <form className="space-y-3" onSubmit={submit} noValidate>
      <div className="space-y-1.5">
        <Label htmlFor={fieldId}>What should change?</Label>
        <Textarea
          id={fieldId}
          value={prompt}
          maxLength={MAX_PROMPT}
          rows={3}
          placeholder="For example: add a checkout page with an order summary and a confirmation message."
          aria-invalid={invalid}
          aria-describedby={`${fieldId}-help`}
          onChange={(event) => {
            setPrompt(event.target.value);
            setInvalid(false);
          }}
        />
        <p id={`${fieldId}-help`} className="text-xs text-muted-foreground">
          {invalid
            ? "Describe the change first."
            : "A new project starts from this project's verified code, plans only the change and runs every check again. This project stays as it is."}
        </p>
      </div>
      <Button type="submit" size="sm" disabled={create.isPending}>
        <GitBranchPlusIcon data-icon="inline-start" />
        {create.isPending ? "Creating…" : "Request change"}
      </Button>
    </form>
  );
}

/** On a change project: which project it changes, linked. */
export function BaseProjectLink({ workflow }: { workflow: Workflow }) {
  const workspace = useWorkspace();
  if (!workflow.base_workflow_id) return null;
  return (
    <p className="text-xs text-muted-foreground">
      Changes{" "}
      <Link className="underline" href={workspacePath(workspace.id, `projects/${workflow.base_workflow_id}`)}>
        the previous project
      </Link>{" "}
      at commit <code>{workflow.base_head_sha?.slice(0, 10)}</code>
    </p>
  );
}
