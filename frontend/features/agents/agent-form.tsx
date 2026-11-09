"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";

import { controlProps, FormError, FormField } from "@/components/common/form-field";
import { ErrorState, ListSkeleton } from "@/components/common/states";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import {
  AgentConfigFields,
  type AgentFormInput,
  agentFormSchema,
  type AgentFormValues,
} from "./agent-config-fields";
import { useCatalog } from "./use-catalog";

const proposalSchema = agentFormSchema.refine((v) => v.rationale.trim().length > 0, {
  path: ["rationale"],
  message: "Explain why this agent is needed.",
});

interface AgentFormProps {
  defaults: AgentFormInput;
  /** Proposals add a rationale and are reviewed by an admin. */
  proposal?: boolean;
  isOwner: boolean;
  submitLabel: string;
  pending: boolean;
  error: string | null;
  onSubmit(values: AgentFormValues): void;
  onCancel(): void;
}

export function AgentForm({ defaults, proposal, isOwner, submitLabel, pending, error, onSubmit, onCancel }: AgentFormProps) {
  const catalog = useCatalog();
  const form = useForm<AgentFormInput, unknown, AgentFormValues>({
    resolver: zodResolver(proposal ? proposalSchema : agentFormSchema),
    defaultValues: defaults,
  });
  const { errors } = form.formState;

  if (catalog.isPending) return <ListSkeleton rows={6} label="Loading agent options" />;
  if (catalog.isError) return <ErrorState error={catalog.error} onRetry={() => void catalog.refetch()} />;

  return (
    <form className="grid gap-5" noValidate onSubmit={form.handleSubmit(onSubmit)}>
      <FormError message={error} />
      <AgentConfigFields
        catalog={catalog.data}
        control={form.control}
        register={form.register}
        errors={errors}
        isOwner={isOwner}
      />
      {proposal ? (
        <FormField id="agent-rationale" label="Why is this agent needed?" error={errors.rationale?.message}>
          <Textarea rows={3} {...controlProps("agent-rationale", errors.rationale?.message)} {...form.register("rationale")} />
        </FormField>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={pending} aria-busy={pending}>
          {pending ? "Saving…" : submitLabel}
        </Button>
        <Button type="button" variant="outline" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
