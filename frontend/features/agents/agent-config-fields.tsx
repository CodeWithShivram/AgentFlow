"use client";

import { Controller, type Control, type FieldErrors, type UseFormRegister } from "react-hook-form";
import { z } from "zod";

import { controlProps, FormField } from "@/components/common/form-field";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import type { AgentCatalog, AgentConfiguration, ToolAccess } from "@/lib/api/types";
import { humanize } from "@/lib/format";

// Mirrors AgentCreateRequest + AgentConfiguration (backend/app/schemas/agents.py). The API
// re-validates everything, including credential-looking text and tool policy.
const SLUG = /^[a-z][a-z0-9_-]{0,63}$/;
const INSTRUCTION_REF = /^[a-z]+:\/\/[A-Za-z0-9._/-]{1,200}$/;

export const agentFormSchema = z.object({
  name: z.string().trim().min(1, "Give the agent a name.").max(100, "Use at most 100 characters."),
  category: z.string().min(1, "Choose a category."),
  description: z.string().max(2000, "Use at most 2,000 characters."),
  instructionRef: z
    .string()
    .trim()
    .regex(INSTRUCTION_REF, "Use a reference such as workspace://writer/v1 (not the instructions text)."),
  modelProfile: z.string().trim().regex(SLUG, "Lowercase letters, digits, - and _ only."),
  temperature: z.coerce.number<string>().min(0, "0 to 2.").max(2, "0 to 2."),
  maxOutputTokens: z.coerce.number<string>().int("Use a whole number.").min(1).max(200_000, "At most 200,000."),
  timeoutSeconds: z.coerce.number<string>().int("Use a whole number.").min(1).max(3600, "At most 3,600 seconds."),
  capabilities: z.array(z.string()).min(1, "Choose at least one capability.").max(20, "At most 20."),
  tools: z.record(z.string(), z.enum(["none", "read", "write"])),
  // Used only by proposals (required there).
  rationale: z.string().max(2000, "Use at most 2,000 characters."),
});
export type AgentFormInput = z.input<typeof agentFormSchema>;
export type AgentFormValues = z.output<typeof agentFormSchema>;

/** Builds a configuration; fields the form does not edit are kept from `base`. */
export function toConfiguration(values: AgentFormValues, base?: AgentConfiguration | null): AgentConfiguration {
  return {
    schema_version: 1,
    system_instruction_ref: values.instructionRef,
    model: {
      ...base?.model,
      profile: values.modelProfile,
      temperature: values.temperature,
      max_output_tokens: values.maxOutputTokens,
    },
    capabilities: values.capabilities,
    tools: Object.entries(values.tools)
      .filter(([, access]) => access !== "none")
      .map(([tool, access]) => ({ tool, access: access as ToolAccess })),
    input_schema_version: base?.input_schema_version ?? 1,
    output_schema_version: base?.output_schema_version ?? 1,
    limits: { ...base?.limits, timeout_seconds: values.timeoutSeconds },
  };
}

export function agentFormDefaults(
  source?: { name: string; category: string; description: string; configuration: AgentConfiguration | null },
): AgentFormInput {
  const config = source?.configuration;
  return {
    name: source?.name ?? "",
    category: source?.category ?? "custom",
    description: source?.description ?? "",
    instructionRef: config?.system_instruction_ref ?? "",
    modelProfile: config?.model.profile ?? "default",
    temperature: String(config?.model.temperature ?? 0.2),
    maxOutputTokens: String(config?.model.max_output_tokens ?? 4096),
    timeoutSeconds: String(config?.limits.timeout_seconds ?? 600),
    capabilities: config?.capabilities ?? [],
    tools: Object.fromEntries((config?.tools ?? []).map((t) => [t.tool, t.access])),
    rationale: "",
  };
}

interface AgentConfigFieldsProps {
  catalog: AgentCatalog;
  control: Control<AgentFormInput, unknown, AgentFormValues>;
  register: UseFormRegister<AgentFormInput>;
  errors: FieldErrors<AgentFormInput>;
  isOwner: boolean;
}

export function AgentConfigFields({ catalog, control, register, errors, isOwner }: AgentConfigFieldsProps) {
  return (
    <div className="grid gap-5">
      <div className="grid gap-5 sm:grid-cols-2">
        <FormField id="agent-name" label="Name" error={errors.name?.message}>
          <Input {...controlProps("agent-name", errors.name?.message)} {...register("name")} />
        </FormField>
        <FormField id="agent-category" label="Category" error={errors.category?.message}>
          <Controller
            control={control}
            name="category"
            render={({ field }) => (
              <Select value={field.value} onValueChange={field.onChange}>
                <SelectTrigger className="w-full" {...controlProps("agent-category", errors.category?.message)}>
                  <SelectValue placeholder="Choose a category" />
                </SelectTrigger>
                <SelectContent>
                  {catalog.categories.map((c) => (
                    <SelectItem key={c} value={c}>
                      {humanize(c)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          />
        </FormField>
      </div>
      <FormField id="agent-description" label="Description" error={errors.description?.message}>
        <Textarea rows={2} {...controlProps("agent-description", errors.description?.message)} {...register("description")} />
      </FormField>
      <FormField
        id="agent-instruction-ref"
        label="Instruction reference"
        error={errors.instructionRef?.message}
        hint="Agents store a reference to versioned instructions, never the text or any credentials."
      >
        <Input
          placeholder="workspace://my-agent/v1"
          {...controlProps("agent-instruction-ref", errors.instructionRef?.message, true)}
          {...register("instructionRef")}
        />
      </FormField>

      <fieldset className="grid gap-2">
        <legend className="mb-1 text-sm font-medium">Capabilities</legend>
        <Controller
          control={control}
          name="capabilities"
          render={({ field }) => (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {catalog.capabilities.map((c) => (
                <label key={c} className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={field.value.includes(c)}
                    onCheckedChange={(checked) =>
                      field.onChange(checked ? [...field.value, c] : field.value.filter((v) => v !== c))
                    }
                  />
                  {humanize(c)}
                </label>
              ))}
            </div>
          )}
        />
        {errors.capabilities?.message ? (
          <p className="text-sm text-destructive">{errors.capabilities.message}</p>
        ) : null}
      </fieldset>

      <fieldset className="grid gap-2">
        <legend className="mb-1 text-sm font-medium">Tools</legend>
        <p className="text-xs text-muted-foreground">
          Tools come only from the platform catalog. High-risk tools require the Owner role. Agents do
          not execute tools until later phases.
        </p>
        <Controller
          control={control}
          name="tools"
          render={({ field }) => (
            <ul className="divide-y rounded-md border">
              {catalog.tools.map((tool) => {
                const locked = tool.risk === "HIGH" && !isOwner;
                const value = field.value[tool.name] ?? "none";
                return (
                  <li key={tool.name} className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center">
                    <div className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2 text-sm font-medium">
                        {tool.name}
                        <Badge variant={tool.risk === "HIGH" ? "destructive" : "outline"}>{humanize(tool.risk)} risk</Badge>
                        {!tool.available_in_phase_1 ? <Badge variant="secondary">Not available yet</Badge> : null}
                      </span>
                      <span className="block text-xs text-muted-foreground">{tool.description}</span>
                    </div>
                    <Select
                      value={value}
                      disabled={locked && value === "none"}
                      onValueChange={(access) => field.onChange({ ...field.value, [tool.name]: access })}
                    >
                      <SelectTrigger className="w-36" aria-label={`Access to ${tool.name}`}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">No access</SelectItem>
                        <SelectItem value="read">Read</SelectItem>
                        <SelectItem value="write">Write</SelectItem>
                      </SelectContent>
                    </Select>
                  </li>
                );
              })}
            </ul>
          )}
        />
      </fieldset>

      <fieldset className="grid gap-4 sm:grid-cols-2">
        <legend className="mb-2 text-sm font-medium">Model and limits</legend>
        <FormField id="agent-model-profile" label="Model profile" error={errors.modelProfile?.message} hint="Resolved by the model gateway.">
          <Input {...controlProps("agent-model-profile", errors.modelProfile?.message, true)} {...register("modelProfile")} />
        </FormField>
        <FormField id="agent-temperature" label="Temperature" error={errors.temperature?.message}>
          <Input type="number" step="0.1" min={0} max={2} {...controlProps("agent-temperature", errors.temperature?.message)} {...register("temperature")} />
        </FormField>
        <FormField id="agent-max-output" label="Max output tokens" error={errors.maxOutputTokens?.message}>
          <Input type="number" min={1} {...controlProps("agent-max-output", errors.maxOutputTokens?.message)} {...register("maxOutputTokens")} />
        </FormField>
        <FormField id="agent-timeout" label="Timeout (seconds)" error={errors.timeoutSeconds?.message}>
          <Input type="number" min={1} max={3600} {...controlProps("agent-timeout", errors.timeoutSeconds?.message)} {...register("timeoutSeconds")} />
        </FormField>
      </fieldset>
    </div>
  );
}
