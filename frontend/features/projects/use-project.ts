"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef } from "react";

import { api } from "@/lib/api";
import type { ProjectProgress, Uuid } from "@/lib/api/types";
import { fetchAllTasks, pollDelay, workflowKeys } from "@/features/workflows/use-workflow";

import { progressIsLive } from "./phase";

/** Project data lives under the workflow's keys, so workflow invalidations refresh it too. */
export const projectKeys = {
  progress: (workspaceId: Uuid, workflowId: Uuid) =>
    [...workflowKeys.detail(workspaceId, workflowId), "progress"] as const,
  integrations: (workspaceId: Uuid, workflowId: Uuid) =>
    [...workflowKeys.detail(workspaceId, workflowId), "integrations"] as const,
  packages: (workspaceId: Uuid, workflowId: Uuid) =>
    [...workflowKeys.detail(workspaceId, workflowId), "packages"] as const,
};

/**
 * Live view of one software project. There is no push channel, so while the project is not
 * finished its progress is polled with the workflow page's backoff; when progress changes, the
 * workflow, its tasks, integration runs and packages are refreshed with it.
 */
export function useProject(workspaceId: Uuid, workflowId: Uuid) {
  const queryClient = useQueryClient();
  const unchangedPolls = useRef(0);

  const workflow = useQuery({
    queryKey: workflowKeys.detail(workspaceId, workflowId),
    queryFn: () => api.workflows.get(workspaceId, workflowId),
  });

  const progressKey = projectKeys.progress(workspaceId, workflowId);
  const progress = useQuery({
    queryKey: progressKey,
    enabled: workflow.isSuccess,
    staleTime: 0,
    queryFn: async () => {
      const previous = queryClient.getQueryData<ProjectProgress>(progressKey);
      const next = await api.projects.progress(workspaceId, workflowId);
      if (previous && JSON.stringify(previous) !== JSON.stringify(next)) {
        unchangedPolls.current = 0;
        void queryClient.invalidateQueries({
          queryKey: workflowKeys.detail(workspaceId, workflowId),
          predicate: (q) => q.queryKey.at(-1) !== "progress",
        });
      } else if (previous) {
        unchangedPolls.current += 1;
      }
      return next;
    },
    refetchInterval: (query) =>
      progressIsLive(query.state.data)
        ? pollDelay(unchangedPolls.current, query.state.fetchFailureCount > 0)
        : false,
  });

  const tasks = useQuery({
    queryKey: workflowKeys.tasks(workspaceId, workflowId),
    queryFn: () => fetchAllTasks(workspaceId, workflowId),
    enabled: workflow.isSuccess,
  });
  const integrations = useQuery({
    queryKey: projectKeys.integrations(workspaceId, workflowId),
    queryFn: () => api.projects.integrations(workspaceId, workflowId),
    enabled: workflow.isSuccess,
  });
  const packages = useQuery({
    queryKey: projectKeys.packages(workspaceId, workflowId),
    queryFn: () => api.projects.packages(workspaceId, workflowId),
    enabled: workflow.isSuccess,
  });

  /** After a change made here (e.g. a package request): poll fast again and refresh everything. */
  const refresh = useCallback(() => {
    unchangedPolls.current = 0;
    return queryClient.invalidateQueries({ queryKey: workflowKeys.detail(workspaceId, workflowId) });
  }, [queryClient, workspaceId, workflowId]);

  return { workflow, progress, tasks, integrations, packages, live: progressIsLive(progress.data), refresh };
}
