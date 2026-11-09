"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { useEffect } from "react";
import { Controller, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { controlProps, FormError, FormField } from "@/components/common/form-field";
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
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { ApiError, describeError } from "@/lib/api/errors";
import type { AcceptanceCriterion, Task, TaskInput } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";

import { useCatalog } from "../agents/use-catalog";

// Mirrors TaskCreateRequest / TaskUpdateRequest (backend/app/schemas/workflows.py).
export const taskSchema = z.object({
  title: z.string().trim().min(1, "Give the task a title.").max(300, "Use at most 300 characters."),
  description: z.string().max(20_000, "Use at most 20,000 characters."),
  requiredSkills: z.array(z.string()).max(20, "Choose at most 20 skills."),
  priority: z.coerce
    .number<string>()
    .int("Use a whole number.")
    .min(-100, "At least -100.")
    .max(100, "At most 100."),
  required: z.boolean(),
  criteria: z
    .string()
    .refine((v) => criteriaLines(v).length <= 50, "Use at most 50 criteria.")
    .refine((v) => criteriaLines(v).every((l) => l.length <= 2000), "Keep each criterion under 2,000 characters."),
  dependsOn: z.array(z.string()).max(50),
});
type FormInput = z.input<typeof taskSchema>;
type Values = z.output<typeof taskSchema>;

function criteriaLines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/** One criterion per line; ids are stable positions (c1, c2, ...) as the API expects slugs. */
export function toCriteria(text: string): AcceptanceCriterion[] {
  return criteriaLines(text).map((description, index) => ({ id: `c${index + 1}`, description }));
}

interface TaskFormDialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  workflowId: string;
  task: Task | null;
  tasks: Task[];
  onSaved(): void;
  onError(error: unknown): void;
}

export function TaskFormDialog({ open, onOpenChange, workflowId, task, tasks, onSaved, onError }: TaskFormDialogProps) {
  const workspace = useWorkspace();
  const catalog = useCatalog();
  const editing = task !== null;
  const form = useForm<FormInput, unknown, Values>({ resolver: zodResolver(taskSchema) });
  const { errors } = form.formState;

  useEffect(() => {
    if (!open) return;
    form.reset({
      title: task?.title ?? "",
      description: task?.description ?? "",
      requiredSkills: task?.required_skills ?? [],
      priority: String(task?.priority ?? 0),
      required: task?.required ?? true,
      criteria: (task?.acceptance_criteria ?? []).map((c) => c.description).join("\n"),
      dependsOn: [],
    });
  }, [open, task, form]);

  const save = useMutation({
    mutationFn: (values: Values) => {
      const body: Omit<TaskInput, "depends_on"> = {
        title: values.title,
        description: values.description,
        required_skills: values.requiredSkills,
        acceptance_criteria: toCriteria(values.criteria),
        priority: values.priority,
        required: values.required,
      };
      if (task) return api.tasks.update(workspace.id, workflowId, task.id, { ...body, revision: task.revision });
      return api.tasks.create(workspace.id, workflowId, { ...body, depends_on: values.dependsOn });
    },
    onSuccess: () => {
      toast.success(editing ? "Task updated" : "Task added");
      onSaved();
      onOpenChange(false);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.isRevisionConflict) {
        onOpenChange(false);
        onError(error);
      }
    },
  });

  const otherTasks = tasks.filter((t) => !t.removed && t.id !== task?.id);

  return (
    <Dialog open={open} onOpenChange={(next) => !save.isPending && onOpenChange(next)}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{editing ? "Edit task" : "Add task"}</DialogTitle>
          <DialogDescription>
            {editing ? "Changes apply to the plan; dependencies are managed separately." : "Describe one unit of work for an agent."}
          </DialogDescription>
        </DialogHeader>
        <form id="task-form" className="grid gap-4" noValidate onSubmit={form.handleSubmit((v) => save.mutate(v))}>
          <FormError message={save.isError && !(save.error instanceof ApiError && save.error.isRevisionConflict) ? describeError(save.error) : null} />
          <FormField id="task-title" label="Title" error={errors.title?.message}>
            <Input {...controlProps("task-title", errors.title?.message)} {...form.register("title")} />
          </FormField>
          <FormField id="task-description" label="Description" error={errors.description?.message}>
            <Textarea rows={3} {...controlProps("task-description", errors.description?.message)} {...form.register("description")} />
          </FormField>
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField id="task-priority" label="Priority" error={errors.priority?.message} hint="-100 to 100; higher runs first.">
              <Input type="number" min={-100} max={100} {...controlProps("task-priority", errors.priority?.message, true)} {...form.register("priority")} />
            </FormField>
            <div className="flex items-center gap-3 self-center">
              <Controller
                control={form.control}
                name="required"
                render={({ field }) => <Switch id="task-required" checked={field.value} onCheckedChange={field.onChange} />}
              />
              <label htmlFor="task-required" className="text-sm">
                Required for the workflow to complete
              </label>
            </div>
          </div>
          <fieldset className="grid gap-2">
            <legend className="mb-1 text-sm font-medium">Required skills</legend>
            {catalog.isPending ? (
              <p className="text-sm text-muted-foreground">Loading skills…</p>
            ) : catalog.isError ? (
              <p className="text-sm text-destructive">Skills could not be loaded.</p>
            ) : (
              <Controller
                control={form.control}
                name="requiredSkills"
                render={({ field }) => (
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                    {catalog.data.capabilities.map((skill) => (
                      <label key={skill} className="flex items-center gap-2 text-sm">
                        <Checkbox
                          checked={field.value.includes(skill)}
                          onCheckedChange={(checked) =>
                            field.onChange(checked ? [...field.value, skill] : field.value.filter((s) => s !== skill))
                          }
                        />
                        {humanize(skill)}
                      </label>
                    ))}
                  </div>
                )}
              />
            )}
            <p className="text-xs text-muted-foreground">Only agents with every required skill can be assigned.</p>
          </fieldset>
          <FormField id="task-criteria" label="Acceptance criteria" error={errors.criteria?.message} hint="One per line.">
            <Textarea rows={3} {...controlProps("task-criteria", errors.criteria?.message, true)} {...form.register("criteria")} />
          </FormField>
          {!editing && otherTasks.length > 0 ? (
            <fieldset className="grid gap-2">
              <legend className="mb-1 text-sm font-medium">Depends on</legend>
              <Controller
                control={form.control}
                name="dependsOn"
                render={({ field }) => (
                  <div className="grid gap-2">
                    {otherTasks.map((t) => (
                      <label key={t.id} className="flex items-center gap-2 text-sm">
                        <Checkbox
                          checked={field.value.includes(t.id)}
                          onCheckedChange={(checked) =>
                            field.onChange(checked ? [...field.value, t.id] : field.value.filter((id) => id !== t.id))
                          }
                        />
                        <span className="truncate">{t.title}</span>
                      </label>
                    ))}
                  </div>
                )}
              />
            </fieldset>
          ) : null}
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={save.isPending}>
            Cancel
          </Button>
          <Button type="submit" form="task-form" disabled={save.isPending} aria-busy={save.isPending}>
            {save.isPending ? "Saving…" : editing ? "Save task" : "Add task"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
