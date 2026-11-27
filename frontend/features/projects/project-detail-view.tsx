"use client";

import { AlertTriangleIcon, RadioIcon, SlidersHorizontalIcon } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { ErrorState, PageSkeleton } from "@/components/common/states";
import { StatusBadge } from "@/components/common/status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { ProjectProgress, Uuid } from "@/lib/api/types";
import { workspacePath } from "@/lib/navigation";
import { useCan, useWorkspace } from "@/lib/workspace";

import { Deliverables, PackageNowAction } from "./deliverables";
import { IntegrationRuns } from "./integration-runs";
import { latestPreview, UiPreview } from "./ui-preview";
import { PACKAGEABLE_STATUSES, projectName } from "./phase";
import { PhaseBadge, ProgressSummary } from "./project-progress";
import { ProjectTasks } from "./project-tasks";
import { useProject } from "./use-project";

export function ProjectDetailView() {
  const workspace = useWorkspace();
  const { projectId } = useParams<{ projectId: string }>();
  const isAdmin = useCan("ADMIN");
  const { workflow, progress, tasks, integrations, packages, live, refresh } = useProject(workspace.id, projectId);

  if (workflow.isPending) return <PageSkeleton />;
  if (workflow.isError) return <ErrorState error={workflow.error} onRetry={() => void workflow.refetch()} />;
  const wf = workflow.data;
  const controlsHref = workspacePath(workspace.id, `workflows/${wf.id}`);
  const name = projectName(wf);

  return (
    <div className="space-y-6">
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          {progress.data ? <PhaseBadge phase={progress.data.phase} /> : <StatusBadge status={wf.status} />}
          {live ? (
            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground" aria-live="off">
              <RadioIcon className="size-3.5 animate-pulse" aria-hidden /> Live updates on
            </span>
          ) : null}
          {wf.project_spec?.stack_profile ? (
            <span className="text-xs text-muted-foreground">Stack: {wf.project_spec.stack_profile}</span>
          ) : null}
        </div>
        <h1 className="text-2xl font-semibold tracking-tight break-words">{name}</h1>
        <p className="text-sm break-words whitespace-pre-wrap text-muted-foreground">{wf.prompt}</p>
        <Button variant="outline" size="sm" asChild>
          <Link href={controlsHref}>
            <SlidersHorizontalIcon data-icon="inline-start" />
            Plan & controls
          </Link>
        </Button>
      </header>

      {progress.data ? (
        <NeedsAttention workspaceId={workspace.id} controlsHref={controlsHref} progress={progress.data} />
      ) : null}

      {progress.isPending ? (
        <Skeleton className="h-48 w-full" role="status" aria-label="Loading progress" />
      ) : progress.isError ? (
        <ErrorState error={progress.error} onRetry={() => void progress.refetch()} />
      ) : (
        <ProgressSummary progress={progress.data} />
      )}

      <Card role="region" aria-label="Tasks">
        <CardHeader>
          <CardTitle>
            <h2>Tasks</h2>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {tasks.isPending ? (
            <p className="text-sm text-muted-foreground">Loading tasks…</p>
          ) : tasks.isError ? (
            <ErrorState error={tasks.error} onRetry={() => void tasks.refetch()} />
          ) : (
            <ProjectTasks tasks={tasks.data} />
          )}
        </CardContent>
      </Card>

      <Card role="region" aria-label="Integration">
        <CardHeader>
          <CardTitle>
            <h2>Integration</h2>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {integrations.isPending ? (
            <p className="text-sm text-muted-foreground">Loading integration runs…</p>
          ) : integrations.isError ? (
            <ErrorState error={integrations.error} onRetry={() => void integrations.refetch()} />
          ) : (
            <IntegrationRuns runs={integrations.data} />
          )}
        </CardContent>
      </Card>

      {integrations.data && latestPreview(integrations.data) ? (
        <Card role="region" aria-label="UI preview">
          <CardHeader>
            <CardTitle>
              <h2>UI preview</h2>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <UiPreview workflowId={wf.id} runs={integrations.data} />
          </CardContent>
        </Card>
      ) : null}

      <Card role="region" aria-label="Deliverables">
        <CardHeader>
          <CardTitle>
            <h2>Deliverables</h2>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {isAdmin && PACKAGEABLE_STATUSES.has(wf.status) ? (
            <PackageNowAction workflowId={wf.id} onRequested={() => void refresh()} />
          ) : null}
          {packages.isPending ? (
            <p className="text-sm text-muted-foreground">Loading packages…</p>
          ) : packages.isError ? (
            <ErrorState error={packages.error} onRetry={() => void packages.refetch()} />
          ) : (
            <Deliverables workflowId={wf.id} packages={packages.data} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function NeedsAttention({
  workspaceId,
  controlsHref,
  progress,
}: {
  workspaceId: Uuid;
  controlsHref: string;
  progress: ProjectProgress;
}) {
  if (progress.phase !== "NEEDS_ATTENTION" && progress.open_requests === 0) return null;
  const requests = progress.open_requests;
  return (
    <Alert>
      <AlertTriangleIcon aria-hidden />
      <AlertTitle>Needs attention</AlertTitle>
      <AlertDescription>
        <p>
          {requests > 0
            ? `${requests} open ${requests === 1 ? "request is" : "requests are"} waiting for a person.`
            : "The project cannot continue on its own."}{" "}
          Review it to keep the project moving.
        </p>
        <p className="mt-1 flex flex-wrap gap-x-4">
          <Link className="underline" href={workspacePath(workspaceId, "requests")}>
            Open requests
          </Link>
          <Link className="underline" href={controlsHref}>
            Plan & controls
          </Link>
        </p>
      </AlertDescription>
    </Alert>
  );
}
