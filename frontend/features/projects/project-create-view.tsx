"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { controlProps, FormError, FormField } from "@/components/common/form-field";
import { PageHeader } from "@/components/common/page-header";
import { EmptyState } from "@/components/common/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  toWorkflowInput,
  workflowFormDefaults,
  type WorkflowFormInput,
  type WorkflowFormValues,
  workflowSchema,
  WorkflowSettingsFields,
} from "@/features/workflows/workflow-create-view";
import { api } from "@/lib/api";
import { describeError } from "@/lib/api/errors";
import type { WorkflowInput } from "@/lib/api/types";
import { useIdempotencyKey } from "@/lib/hooks/use-idempotency-key";
import { workspacePath } from "@/lib/navigation";
import { useCan, useWorkspace } from "@/lib/workspace";

/** The create payload of a software project: the workflow's, with kind SOFTWARE_PROJECT. */
export function toProjectInput(values: WorkflowFormValues): WorkflowInput {
  return { ...toWorkflowInput(values), kind: "SOFTWARE_PROJECT" };
}

const STEPS = [
  "AgentFlow plans the project: its stack, architecture and tasks.",
  "You review and approve the plan.",
  "Agents build it task by task; each change is verified.",
  "The project is checked in an offline sandbox (compile or type-check, then its tests).",
  "You download it as a ZIP with a build report.",
];

export function ProjectCreateView() {
  const workspace = useWorkspace();
  const canCreate = useCan("MEMBER");
  const router = useRouter();
  const queryClient = useQueryClient();
  // The same key for a retried identical request; a new key once the request changes.
  const idempotencyKeyFor = useIdempotencyKey();
  const form = useForm<WorkflowFormInput, unknown, WorkflowFormValues>({
    resolver: zodResolver(workflowSchema),
    defaultValues: workflowFormDefaults,
  });
  const { errors } = form.formState;
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const create = useMutation({
    mutationFn: (values: WorkflowFormValues) => {
      const input = toProjectInput(values);
      return api.workflows.create(workspace.id, input, idempotencyKeyFor(input));
    },
    onSuccess: (workflow) => {
      void queryClient.invalidateQueries({ queryKey: ["workflows", workspace.id] });
      toast.success("Project created");
      router.push(workspacePath(workspace.id, `projects/${workflow.id}`));
    },
  });

  if (!canCreate) {
    return (
      <EmptyState
        title="You cannot create projects"
        description="Viewers can follow projects but not create them. Ask an admin for the Member role."
      />
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader title="New project" description="Describe the software you want." />
      <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground" aria-label="What happens next">
        {STEPS.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      <Card>
        <CardContent>
          <form
            className="grid gap-5"
            noValidate
            onSubmit={form.handleSubmit(
              (values) => create.mutate(values),
              // A settings error must not hide inside the collapsed section.
              (invalid) => {
                if (Object.keys(invalid).some((field) => field !== "prompt")) setAdvancedOpen(true);
              },
            )}
          >
            <FormError message={create.isError ? describeError(create.error) : null} />
            <FormField
              id="prompt"
              label="What should it build?"
              error={errors.prompt?.message}
              hint="Describe what the software does, who uses it and any technology you need."
            >
              <Textarea
                rows={8}
                {...controlProps("prompt", errors.prompt?.message, true)}
                {...form.register("prompt")}
              />
            </FormField>

            <details
              className="rounded-lg border p-3"
              open={advancedOpen}
              onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}
            >
              <summary className="cursor-pointer text-sm font-medium">Advanced settings</summary>
              <div className="pt-4">
                <WorkflowSettingsFields form={form} />
              </div>
            </details>

            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={create.isPending} aria-busy={create.isPending}>
                {create.isPending ? "Creating…" : "Create project"}
              </Button>
              <Button variant="outline" asChild>
                <Link href={workspacePath(workspace.id, "projects")}>Cancel</Link>
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
