"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { PageHeader } from "@/components/common/page-header";
import { RelativeTime } from "@/components/common/relative-time";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/common/states";
import { api } from "@/lib/api";
import type { Workflow, WorkflowEvent } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { workspacePath } from "@/lib/navigation";
import { useWorkspace } from "@/lib/workspace";

import { describeEvent } from "../workflows/event-timeline";

const RECENT_WORKFLOWS = 10;
const EVENTS_PER_WORKFLOW = 500;
const SHOWN = 50;

interface ActivityItem {
  workflow: Workflow;
  event: WorkflowEvent;
}

/**
 * The API has no workspace-wide activity feed, so this merges the event logs of the ten most
 * recently created workflows (first 500 events each). Older workflows are not included.
 */
async function loadActivity(workspaceId: string): Promise<ActivityItem[]> {
  const workflows = await api.workflows.list(workspaceId, { limit: RECENT_WORKFLOWS });
  const pages = await Promise.all(
    workflows.items.map(async (workflow) => {
      const page = await api.workflows.events(workspaceId, workflow.id, { limit: EVENTS_PER_WORKFLOW });
      return page.items.map((event) => ({ workflow, event }));
    }),
  );
  return pages
    .flat()
    .sort((a, b) => b.event.created_at.localeCompare(a.event.created_at))
    .slice(0, SHOWN);
}

export function ActivityView() {
  const workspace = useWorkspace();
  const activity = useQuery({
    queryKey: ["workflows", workspace.id, "activity"],
    queryFn: () => loadActivity(workspace.id),
    refetchInterval: 30_000,
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Activity"
        description="Recent events from the ten newest workflows. Notifications are not available yet."
      />
      {activity.isPending ? (
        <ListSkeleton label="Loading activity" />
      ) : activity.isError ? (
        <ErrorState error={activity.error} onRetry={() => void activity.refetch()} />
      ) : activity.data.length === 0 ? (
        <EmptyState title="No activity yet" description="Events appear here as workflows change." />
      ) : (
        <ol className="divide-y rounded-lg border" aria-label="Recent activity, newest first">
          {activity.data.map(({ workflow, event }) => (
            <li key={event.id} className="flex flex-col gap-1 p-3 sm:flex-row sm:items-center sm:gap-4">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{humanize(event.event_type)}</p>
                <p className="truncate text-sm text-muted-foreground">{describeEvent(event)}</p>
                <Link
                  href={workspacePath(workspace.id, `workflows/${workflow.id}`)}
                  className="line-clamp-1 text-xs underline-offset-4 hover:underline"
                >
                  {workflow.prompt}
                </Link>
              </div>
              <span className="shrink-0 text-xs text-muted-foreground">
                <RelativeTime value={event.created_at} />
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
