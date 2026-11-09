"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { controlProps, FormError, FormField } from "@/components/common/form-field";
import { PageHeader } from "@/components/common/page-header";
import { EmptyState } from "@/components/common/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { describeError } from "@/lib/api/errors";
import type { WorkflowInput } from "@/lib/api/types";
import { newIdempotencyKey } from "@/lib/format";
import { workspacePath } from "@/lib/navigation";
import { useCan, useWorkspace } from "@/lib/workspace";

// Mirrors WorkflowCreateRequest / WorkflowSettings (backend/app/schemas/workflows.py).
export const workflowSchema = z.object({
  prompt: z
    .string()
    .max(20_000, "Use at most 20,000 characters.")
    .refine((v) => v.trim().length > 0, "Describe the goal."),
  autonomyPolicy: z.enum(["approval_required", "autonomous"]),
  budgetLimit: z
    .string()
    .trim()
    .refine((v) => v === "" || /^\d{1,7}(\.\d{1,4})?$/.test(v), "Use a number with up to 4 decimals.")
    .refine((v) => v === "" || Number(v) <= 1_000_000, "The maximum is 1,000,000."),
  currency: z
    .string()
    .trim()
    .regex(/^[A-Za-z]{3}$/, "Use a three-letter ISO 4217 code, such as USD."),
  maxParallelTasks: z.coerce
    .number<string>()
    .int("Use a whole number.")
    .min(1, "At least 1.")
    .max(100, "At most 100."),
  allowDynamicAgentProposals: z.boolean(),
  deadline: z
    .string()
    .refine((v) => v === "" || new Date(v).getTime() > Date.now(), "The deadline must be in the future."),
});
type FormInput = z.input<typeof workflowSchema>;
type Values = z.output<typeof workflowSchema>;

export function toWorkflowInput(values: Values): WorkflowInput {
  return {
    prompt: values.prompt,
    settings: {
      autonomy_policy: values.autonomyPolicy,
      budget_limit: values.budgetLimit === "" ? null : values.budgetLimit,
      currency: values.currency.toUpperCase(),
      max_parallel_tasks: values.maxParallelTasks,
      allow_dynamic_agent_proposals: values.allowDynamicAgentProposals,
      // datetime-local has no zone; the browser's zone makes it an absolute instant.
      deadline: values.deadline === "" ? null : new Date(values.deadline).toISOString(),
    },
  };
}

export function WorkflowCreateView() {
  const workspace = useWorkspace();
  const canCreate = useCan("MEMBER");
  const router = useRouter();
  const queryClient = useQueryClient();
  // One key per form: a retried submission of the same form cannot create a second workflow.
  const [idempotencyKey] = useState(newIdempotencyKey);
  const form = useForm<FormInput, unknown, Values>({
    resolver: zodResolver(workflowSchema),
    defaultValues: {
      prompt: "",
      autonomyPolicy: "approval_required",
      budgetLimit: "",
      currency: "USD",
      maxParallelTasks: "3",
      allowDynamicAgentProposals: false,
      deadline: "",
    },
  });
  const { errors } = form.formState;

  const create = useMutation({
    mutationFn: (values: Values) =>
      api.workflows.create(workspace.id, toWorkflowInput(values), idempotencyKey),
    onSuccess: (workflow) => {
      void queryClient.invalidateQueries({ queryKey: ["workflows", workspace.id] });
      toast.success("Workflow created");
      router.push(workspacePath(workspace.id, `workflows/${workflow.id}`));
    },
  });

  if (!canCreate) {
    return (
      <EmptyState
        title="You cannot create workflows"
        description="Viewers can follow workflows but not create them. Ask an admin for the Member role."
      />
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader title="New workflow" description="Describe the goal. You add tasks while planning." />
      <Card>
        <CardContent>
          <form
            className="grid gap-5"
            noValidate
            onSubmit={form.handleSubmit((values) => create.mutate(values))}
          >
            <FormError message={create.isError ? describeError(create.error) : null} />
            <FormField id="prompt" label="Goal" error={errors.prompt?.message} hint="What should the agents achieve?">
              <Textarea
                rows={5}
                {...controlProps("prompt", errors.prompt?.message, true)}
                {...form.register("prompt")}
              />
            </FormField>

            <div className="grid gap-5 sm:grid-cols-2">
              <FormField
                id="autonomyPolicy"
                label="Plan approval"
                hint="Approval required: an admin approves the plan before it can run."
              >
                <Controller
                  control={form.control}
                  name="autonomyPolicy"
                  render={({ field }) => (
                    <Select value={field.value} onValueChange={field.onChange}>
                      <SelectTrigger {...controlProps("autonomyPolicy", undefined, true)} className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="approval_required">Approval required</SelectItem>
                        <SelectItem value="autonomous">Autonomous</SelectItem>
                      </SelectContent>
                    </Select>
                  )}
                />
              </FormField>
              <FormField id="maxParallelTasks" label="Maximum parallel tasks" error={errors.maxParallelTasks?.message}>
                <Input
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={100}
                  {...controlProps("maxParallelTasks", errors.maxParallelTasks?.message)}
                  {...form.register("maxParallelTasks")}
                />
              </FormField>
              <FormField
                id="budgetLimit"
                label="Budget limit (optional)"
                error={errors.budgetLimit?.message}
                hint="A cap recorded with the workflow. Spend is not measured until Phase 2."
              >
                <Input
                  inputMode="decimal"
                  placeholder="No limit"
                  {...controlProps("budgetLimit", errors.budgetLimit?.message, true)}
                  {...form.register("budgetLimit")}
                />
              </FormField>
              <FormField id="currency" label="Currency" error={errors.currency?.message}>
                <Input
                  maxLength={3}
                  autoCapitalize="characters"
                  className="uppercase"
                  {...controlProps("currency", errors.currency?.message)}
                  {...form.register("currency")}
                />
              </FormField>
              <FormField id="deadline" label="Deadline (optional)" error={errors.deadline?.message}>
                <Input
                  type="datetime-local"
                  {...controlProps("deadline", errors.deadline?.message)}
                  {...form.register("deadline")}
                />
              </FormField>
              <div className="flex items-center gap-3 self-end pb-2">
                <Controller
                  control={form.control}
                  name="allowDynamicAgentProposals"
                  render={({ field }) => (
                    <Switch
                      id="allowDynamicAgentProposals"
                      checked={field.value}
                      onCheckedChange={field.onChange}
                    />
                  )}
                />
                <label htmlFor="allowDynamicAgentProposals" className="text-sm">
                  Allow agent proposals during this workflow
                </label>
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={create.isPending} aria-busy={create.isPending}>
                {create.isPending ? "Creating…" : "Create workflow"}
              </Button>
              <Button variant="outline" asChild>
                <Link href={workspacePath(workspace.id, "workflows")}>Cancel</Link>
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
