"use client";

import { useMutation } from "@tanstack/react-query";
import { XIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { FormError } from "@/components/common/form-field";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { describeError } from "@/lib/api/errors";
import type { Task } from "@/lib/api/types";
import { useWorkspace } from "@/lib/workspace";

interface DependenciesDialogProps {
  task: Task | null;
  tasks: Task[];
  workflowId: string;
  onOpenChange(open: boolean): void;
  onChanged(): void;
}

export function DependenciesDialog({ task, tasks, workflowId, onOpenChange, onChanged }: DependenciesDialogProps) {
  const workspace = useWorkspace();
  const [selected, setSelected] = useState("");
  const byId = new Map(tasks.map((t) => [t.id, t]));

  const add = useMutation({
    mutationFn: (dependsOn: string) => api.tasks.addDependency(workspace.id, workflowId, task!.id, dependsOn),
    onSuccess: () => {
      setSelected("");
      toast.success("Dependency added");
      onChanged();
    },
  });
  const remove = useMutation({
    mutationFn: (dependsOn: string) => api.tasks.removeDependency(workspace.id, workflowId, task!.id, dependsOn),
    onSuccess: () => {
      toast.success("Dependency removed");
      onChanged();
    },
  });
  const pending = add.isPending || remove.isPending;
  const error = add.error ?? remove.error;

  const candidates = task
    ? tasks.filter((t) => !t.removed && t.id !== task.id && !task.depends_on.includes(t.id))
    : [];

  return (
    <Dialog
      open={task !== null}
      onOpenChange={(open) => {
        if (pending) return;
        add.reset();
        remove.reset();
        onOpenChange(open);
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Dependencies</DialogTitle>
          <DialogDescription>
            {task ? <>&ldquo;{task.title}&rdquo; starts only after these tasks complete.</> : null}
          </DialogDescription>
        </DialogHeader>
        <FormError message={error ? describeError(error) : null} />
        {task && task.depends_on.length > 0 ? (
          <ul className="divide-y rounded-md border" aria-label="Current dependencies">
            {task.depends_on.map((id) => (
              <li key={id} className="flex items-center gap-2 px-3 py-2 text-sm">
                <span className="flex-1 truncate">{byId.get(id)?.title ?? "Unknown task"}</span>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Remove dependency on ${byId.get(id)?.title ?? "task"}`}
                  disabled={pending}
                  onClick={() => remove.mutate(id)}
                >
                  <XIcon />
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No dependencies. This task can start first.</p>
        )}
        {candidates.length > 0 ? (
          <div className="grid gap-2">
            <Label htmlFor="add-dependency">Add a dependency</Label>
            <div className="flex gap-2">
              <Select value={selected} onValueChange={setSelected}>
                <SelectTrigger id="add-dependency" className="min-w-0 flex-1">
                  <SelectValue placeholder="Choose a task" />
                </SelectTrigger>
                <SelectContent>
                  {candidates.map((t) => (
                    <SelectItem key={t.id} value={t.id}>
                      {t.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button disabled={!selected || pending} onClick={() => add.mutate(selected)}>
                {add.isPending ? "Adding…" : "Add"}
              </Button>
            </div>
          </div>
        ) : null}
        <DialogFooter>
          <Button variant="outline" disabled={pending} onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
