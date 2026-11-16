"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";
import { interventionKeys, RequestList } from "@/features/interventions/request-list";

/**
 * The workflow's open requests for people (ADR-0035): questions, approvals and escalated tasks.
 * Refreshed with the workflow (`version` changes whenever the workflow or its events do). The
 * previous result stays shown while a refresh loads, so the section never blinks out and shifts
 * the page below it on every poll.
 */
export function NeedsInput({ workflowId, version }: { workflowId: string; version: string }) {
  const workspace = useWorkspace();
  const query = useQuery({
    queryKey: [...interventionKeys.all(workspace.id), "workflow", workflowId, version],
    queryFn: () =>
      api.interventions.list(workspace.id, { workflow_id: workflowId, status: "OPEN", limit: 100 }),
    placeholderData: keepPreviousData,
  });
  const items = query.data?.items ?? [];
  if (items.length === 0) return null;
  return (
    <Card role="region" aria-label="Needs your input">
      <CardHeader>
        <CardTitle>
          <h2>Needs your input ({items.length})</h2>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <RequestList items={items} showWorkflow={false} />
      </CardContent>
    </Card>
  );
}
