"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { api } from "@/lib/api";
import type { EventPage, Task, Uuid, WorkflowEvent } from "@/lib/api/types";

import { TASK_IN_FLIGHT, WORKFLOW_ACTIVE } from "./status";

export const workflowKeys = {
  all: (workspaceId: Uuid) => ["workflows", workspaceId] as const,
  detail: (workspaceId: Uuid, workflowId: Uuid) => ["workflows", workspaceId, "detail", workflowId] as const,
  tasks: (workspaceId: Uuid, workflowId: Uuid) => ["workflows", workspaceId, "detail", workflowId, "tasks"] as const,
  events: (workspaceId: Uuid, workflowId: Uuid) => ["workflows", workspaceId, "detail", workflowId, "events"] as const,
  planDrafts: (workspaceId: Uuid, workflowId: Uuid) =>
    ["workflows", workspaceId, "detail", workflowId, "plan-drafts"] as const,
  planDraft: (workspaceId: Uuid, workflowId: Uuid, version: number) =>
    ["workflows", workspaceId, "detail", workflowId, "plan-drafts", version] as const,
  executions: (workspaceId: Uuid, workflowId: Uuid, taskId: Uuid) =>
    ["workflows", workspaceId, "detail", workflowId, "executions", taskId] as const,
};

const TASK_PAGE = 100;
const EVENT_PAGE = 500;
const MAX_EVENT_PAGES_PER_POLL = 10;

/** Polling interval: fast after a change, slowing down while nothing happens. */
export function pollDelay(unchangedPolls: number, failed: boolean): number {
  if (failed) return 15_000;
  return Math.min(2_000 * 1.5 ** unchangedPolls, 15_000);
}

async function fetchAllTasks(workspaceId: Uuid, workflowId: Uuid): Promise<Task[]> {
  const tasks: Task[] = [];
  for (let offset = 0; ; offset += TASK_PAGE) {
    const page = await api.tasks.list(workspaceId, workflowId, { limit: TASK_PAGE, offset });
    tasks.push(...page.items);
    if (tasks.length >= page.total || page.items.length === 0) return tasks;
  }
}

/** Appends events after the last one already loaded (the history is append-only). */
export async function fetchNewEvents(
  load: (after: number) => Promise<EventPage>,
  previous: WorkflowEvent[],
): Promise<WorkflowEvent[]> {
  const events = [...previous];
  let after = events.at(-1)?.sequence ?? 0;
  for (let page = 0; page < MAX_EVENT_PAGES_PER_POLL; page++) {
    const result = await load(after);
    events.push(...result.items);
    if (result.items.length < EVENT_PAGE || result.last_sequence === null) break;
    after = result.last_sequence;
  }
  return events;
}

/**
 * Live view of one workflow. There is no push channel (no SSE/WebSocket endpoint), so while the
 * workflow is executing or its prompt analysis or plan draft is pending, the append-only event log is polled;
 * new events refresh the workflow, its tasks and their executions.
 */
export function useWorkflow(workspaceId: Uuid, workflowId: Uuid) {
  const queryClient = useQueryClient();
  const unchangedPolls = useRef(0);

  const workflow = useQuery({
    queryKey: workflowKeys.detail(workspaceId, workflowId),
    queryFn: () => api.workflows.get(workspaceId, workflowId),
  });
  const tasks = useQuery({
    queryKey: workflowKeys.tasks(workspaceId, workflowId),
    queryFn: () => fetchAllTasks(workspaceId, workflowId),
    enabled: workflow.isSuccess,
  });

  const live =
    (workflow.data !== undefined && WORKFLOW_ACTIVE.has(workflow.data.status)) ||
    workflow.data?.analysis_status === "PENDING" ||
    workflow.data?.plan_status === "PENDING" ||
    (tasks.data ?? []).some((t) => TASK_IN_FLIGHT.has(t.status));

  const eventsKey = workflowKeys.events(workspaceId, workflowId);
  const events = useQuery({
    queryKey: eventsKey,
    enabled: workflow.isSuccess,
    staleTime: 0,
    queryFn: async () => {
      const previous = queryClient.getQueryData<WorkflowEvent[]>(eventsKey) ?? [];
      const next = await fetchNewEvents(
        (after) => api.workflows.events(workspaceId, workflowId, { after, limit: EVENT_PAGE }),
        previous,
      );
      if (next.length > previous.length) {
        unchangedPolls.current = 0;
        if (previous.length > 0) {
          void queryClient.invalidateQueries({
            queryKey: workflowKeys.detail(workspaceId, workflowId),
            predicate: (q) => q.queryKey.at(-1) !== "events",
          });
        }
      } else {
        unchangedPolls.current += 1;
      }
      return next;
    },
    refetchInterval: (query) =>
      live ? pollDelay(unchangedPolls.current, query.state.fetchFailureCount > 0) : false,
  });

  return { workflow, tasks, events, live };
}
