"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRightIcon, CircleCheckIcon, LoaderCircleIcon } from "lucide-react";
import { useState } from "react";

import { RelativeTime } from "@/components/common/relative-time";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/common/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import type { PlanDraftPreview as Preview, PlannedTask, Workflow } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { useCan, useWorkspace } from "@/lib/workspace";

import { ConfirmPlanDialog } from "./confirm-plan-dialog";
import { PLAN_EDITABLE } from "./status";
import { workflowKeys } from "./use-workflow";

/**
 * Full preview of a plan draft with its DAG validation (Step 2.4), from which members confirm a
 * draft into tasks (Step 2.5). All model-generated text is rendered as plain text.
 */
interface PlanDraftPreviewProps {
  workflow: Workflow;
  existingTaskCount: number;
  onConfirmed(): void;
  onConflict(error: unknown): void;
}

export function PlanDraftPreview({ workflow, existingTaskCount, onConfirmed, onConflict }: PlanDraftPreviewProps) {
  const canConfirm = useCan("MEMBER") && PLAN_EDITABLE.has(workflow.status);
  const workspace = useWorkspace();
  const [chosen, setChosen] = useState<number | null>(null);
  const drafts = useQuery({
    queryKey: workflowKeys.planDrafts(workspace.id, workflow.id),
    queryFn: () => api.workflows.planDrafts(workspace.id, workflow.id),
    enabled: workflow.plan_status !== "NOT_REQUESTED",
  });
  const versions = drafts.data?.items ?? [];
  const version = chosen ?? versions[0]?.version ?? null;
  const preview = useQuery({
    queryKey: workflowKeys.planDraft(workspace.id, workflow.id, version ?? 0),
    queryFn: () => api.workflows.planDraft(workspace.id, workflow.id, version ?? 0),
    enabled: version !== null,
  });

  if (workflow.plan_status === "NOT_REQUESTED") {
    return (
      <EmptyState
        title="No plan draft yet"
        description="A draft is generated automatically once the request has been analysed."
      />
    );
  }
  if (drafts.isPending) return <ListSkeleton rows={3} label="Loading plan drafts" />;
  if (drafts.isError) return <ErrorState error={drafts.error} onRetry={() => void drafts.refetch()} />;
  if (versions.length === 0) {
    return workflow.plan_status === "PENDING" ? (
      <div role="status" className="flex items-center gap-3 rounded-lg border p-4 text-sm">
        <LoaderCircleIcon className="size-4 animate-spin" aria-hidden />
        Drafting a plan from the analysis…
      </div>
    ) : (
      <EmptyState title="No plan draft" description="The plan draft could not be generated. See the Requirements tab." />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor="plan-version">Draft version</Label>
          <Select value={String(version)} onValueChange={(v) => setChosen(Number(v))}>
            <SelectTrigger id="plan-version" className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {versions.map((d, index) => (
                <SelectItem key={d.version} value={String(d.version)}>
                  Version {d.version}
                  {index === 0 ? " (latest)" : ""} · {d.task_count} {d.task_count === 1 ? "task" : "tasks"}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {workflow.plan_status === "PENDING" ? (
          <p role="status" className="flex items-center gap-2 pb-2 text-sm text-muted-foreground">
            <LoaderCircleIcon className="size-4 animate-spin" aria-hidden /> A newer draft is being generated…
          </p>
        ) : null}
      </div>
      {preview.isPending ? (
        <ListSkeleton rows={4} label="Loading the plan draft" />
      ) : preview.isError ? (
        <ErrorState error={preview.error} onRetry={() => void preview.refetch()} />
      ) : (
        <DraftView
          preview={preview.data}
          workflow={workflow}
          canConfirm={canConfirm}
          existingTaskCount={existingTaskCount}
          onConfirmed={onConfirmed}
          onConflict={onConflict}
        />
      )}
    </div>
  );
}

interface DraftViewProps {
  preview: Preview;
  workflow: Workflow;
  canConfirm: boolean;
  existingTaskCount: number;
  onConfirmed(): void;
  onConflict(error: unknown): void;
}

function DraftView({ preview, workflow, canConfirm, existingTaskCount, onConfirmed, onConflict }: DraftViewProps) {
  const [confirming, setConfirming] = useState(false);
  const confirmed = workflow.confirmed_plan_version === preview.version;
  const byKey = new Map(preview.plan.tasks.map((t) => [t.key, t]));
  const errors = preview.issues.filter((i) => i.severity === "error");
  const warnings = preview.issues.filter((i) => i.severity === "warning");
  const titles = (keys: string[]) => keys.map((k) => byKey.get(k)?.title ?? k).join(", ");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <p className="min-w-0 flex-1 text-sm">
          <span className="font-medium">{preview.plan.summary}</span>{" "}
          <span className="text-muted-foreground">
            · Version {preview.version}, generated <RelativeTime value={preview.created_at} />
          </span>
          {confirmed ? (
            <Badge variant="secondary" className="ml-2">
              Confirmed
            </Badge>
          ) : null}
        </p>
        {canConfirm && errors.length === 0 ? (
          <Button onClick={() => setConfirming(true)}>{confirmed ? "Confirm again" : "Confirm this draft"}</Button>
        ) : null}
      </div>
      <ConfirmPlanDialog
        open={confirming}
        onOpenChange={setConfirming}
        workflow={workflow}
        preview={preview}
        existingTaskCount={existingTaskCount}
        onConfirmed={onConfirmed}
        onConflict={onConflict}
      />

      {errors.length > 0 ? (
        <Alert variant="destructive">
          <AlertTitle>This draft cannot be used</AlertTitle>
          <AlertDescription>
            <ul className="mt-1 list-disc space-y-1 pl-5">
              {errors.map((issue, i) => (
                <li key={i}>
                  {issue.message} ({titles(issue.task_keys)})
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}
      {warnings.length > 0 ? (
        <Alert>
          <AlertTitle>Check before confirming ({warnings.length})</AlertTitle>
          <AlertDescription>
            <ul className="mt-1 list-disc space-y-1 pl-5">
              {warnings.map((issue, i) => (
                <li key={i}>
                  {issue.message} <span className="text-muted-foreground">({titles(issue.task_keys)})</span>
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}
      {preview.issues.length === 0 ? (
        <p className="flex items-center gap-2 text-sm text-emerald-700 dark:text-emerald-400">
          <CircleCheckIcon className="size-4" aria-hidden /> No problems found.
        </p>
      ) : null}

      <div className="overflow-x-auto pb-2">
        <ol className="flex min-w-max items-start gap-3" aria-label="Draft stages">
          {preview.stages.map((stage, index) => (
            <li key={index} className="flex items-start gap-3">
              <section className="w-64 space-y-2" aria-label={`Draft stage ${index + 1}`}>
                <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                  Stage {index + 1} · {stage.length} parallel
                </h3>
                <ul className="space-y-2">
                  {stage.map((key) => {
                    const task = byKey.get(key);
                    return task ? <TaskCard key={key} task={task} titles={titles} /> : null;
                  })}
                </ul>
              </section>
              {index < preview.stages.length - 1 ? (
                <ArrowRightIcon className="mt-8 size-4 shrink-0 text-muted-foreground" aria-hidden />
              ) : null}
            </li>
          ))}
        </ol>
      </div>

      {preview.plan.risks.length > 0 ? (
        <section>
          <h3 className="mb-1 text-sm font-medium">Risks</h3>
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {preview.plan.risks.map((risk, i) => (
              <li key={i}>{risk}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function TaskCard({ task, titles }: { task: PlannedTask; titles: (keys: string[]) => string }) {
  return (
    <li className="rounded-md border bg-card p-3 text-sm">
      <details>
        <summary className="cursor-pointer list-none">
          <span className="line-clamp-2 font-medium">{task.title}</span>
          <span className="mt-2 flex flex-wrap gap-1">
            {task.required ? (
              <Badge variant="outline">Required</Badge>
            ) : (
              <Badge variant="outline" className="border-dashed text-muted-foreground">
                Optional
              </Badge>
            )}
            {task.required_skills.map((skill) => (
              <Badge key={skill} variant="secondary">
                {humanize(skill)}
              </Badge>
            ))}
          </span>
          {task.depends_on.length > 0 ? (
            <span className="mt-2 block text-xs text-muted-foreground">After: {titles(task.depends_on)}</span>
          ) : null}
        </summary>
        <div className="mt-3 space-y-2 border-t pt-2">
          {task.description ? <p className="whitespace-pre-wrap break-words">{task.description}</p> : null}
          <div>
            <p className="text-xs font-medium text-muted-foreground">Deliverables</p>
            <ul className="list-disc pl-5">
              {task.deliverables.map((d, i) => (
                <li key={i} className="break-words">
                  {d}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground">Acceptance criteria</p>
            <ul className="list-disc pl-5">
              {task.acceptance_criteria.map((c) => (
                <li key={c.id} className="break-words">
                  {c.description}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </details>
    </li>
  );
}
