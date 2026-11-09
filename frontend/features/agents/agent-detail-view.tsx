"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { RelativeTime } from "@/components/common/relative-time";
import { RevisionConflictDialog } from "@/components/common/revision-conflict-dialog";
import { ErrorState, ListSkeleton, PageSkeleton } from "@/components/common/states";
import { StatusBadge } from "@/components/common/status-badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { ApiError, describeError } from "@/lib/api/errors";
import type { Agent } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { useActionErrors } from "@/lib/hooks/use-action-errors";
import { useCan, useWorkspace } from "@/lib/workspace";

import { agentFormDefaults, type AgentFormValues, toConfiguration } from "./agent-config-fields";
import { AgentForm } from "./agent-form";
import { ConfigSummary } from "./config-summary";

type AgentCommand = "publish" | "activate" | "deactivate";

export function AgentDetailView() {
  const workspace = useWorkspace();
  const { agentId } = useParams<{ agentId: string }>();
  const isAdmin = useCan("ADMIN");
  const isOwner = useCan("OWNER");
  const queryClient = useQueryClient();
  const errors = useActionErrors();
  const [editing, setEditing] = useState(false);
  const [confirmDeactivate, setConfirmDeactivate] = useState(false);

  const key = ["agents", workspace.id, "detail", agentId];
  const agent = useQuery({ queryKey: key, queryFn: () => api.agents.get(workspace.id, agentId) });
  const versions = useQuery({
    queryKey: [...key, "versions"],
    queryFn: () => api.agents.versions(workspace.id, agentId, { limit: 100 }),
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["agents", workspace.id] });
  };

  const update = useMutation({
    mutationFn: ({ values, current }: { values: AgentFormValues; current: Agent }) =>
      api.agents.update(workspace.id, agentId, {
        revision: current.revision,
        name: values.name,
        category: values.category,
        description: values.description,
        configuration: toConfiguration(values, current.draft_configuration),
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(key, updated);
      refresh();
      setEditing(false);
      toast.success("Draft saved. Publish it to make it the active version.");
    },
    onError: (error) => {
      if (error instanceof ApiError && error.isRevisionConflict) {
        setEditing(false);
        errors.handle(error);
      }
    },
  });

  const command = useMutation({
    mutationFn: async ({ name, revision }: { name: AgentCommand; revision: number }): Promise<void> => {
      if (name === "publish") await api.agents.publish(workspace.id, agentId, revision);
      else if (name === "activate") await api.agents.activate(workspace.id, agentId, revision);
      else await api.agents.deactivate(workspace.id, agentId, revision);
    },
    onSuccess: (_, { name }) => {
      setConfirmDeactivate(false);
      refresh();
      toast.success(
        name === "publish" ? "New version published" : name === "activate" ? "Agent activated" : "Agent deactivated",
      );
    },
    onError: (error) => {
      setConfirmDeactivate(false);
      errors.handle(error);
    },
  });

  if (agent.isPending) return <PageSkeleton />;
  if (agent.isError) return <ErrorState error={agent.error} onRetry={() => void agent.refetch()} />;
  const a = agent.data;
  const manageable = isAdmin && !a.is_builtin;

  return (
    <div className="space-y-6">
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={a.status} />
          {a.is_builtin ? <Badge variant="outline">Built-in</Badge> : null}
          <span className="text-xs text-muted-foreground">
            {a.active_version ? `Active version ${a.active_version}` : "Not published"} · Revision {a.revision}
          </span>
        </div>
        <h1 className="text-2xl font-semibold tracking-tight break-words">{a.name}</h1>
        <p className="text-sm text-muted-foreground">
          {humanize(a.category)}
          {a.description ? ` — ${a.description}` : ""}
        </p>
        {manageable && !editing ? (
          <div className="flex flex-wrap gap-2">
            {a.has_unpublished_changes ? (
              <Button disabled={command.isPending} onClick={() => command.mutate({ name: "publish", revision: a.revision })}>
                Publish version {(a.active_version ?? 0) + 1}
              </Button>
            ) : null}
            {a.status === "INACTIVE" && a.active_version ? (
              <Button
                variant={a.has_unpublished_changes ? "outline" : "default"}
                disabled={command.isPending}
                onClick={() => command.mutate({ name: "activate", revision: a.revision })}
              >
                Activate
              </Button>
            ) : null}
            {a.status === "ACTIVE" ? (
              <Button variant="outline" disabled={command.isPending} onClick={() => setConfirmDeactivate(true)}>
                Deactivate
              </Button>
            ) : null}
            <Button variant="outline" onClick={() => setEditing(true)}>
              Edit draft
            </Button>
          </div>
        ) : null}
        {a.is_builtin ? (
          <p className="text-sm text-muted-foreground">Built-in agents are managed by the platform and cannot be edited.</p>
        ) : !isAdmin ? (
          <p className="text-sm text-muted-foreground">Only admins can change agents.</p>
        ) : null}
      </header>

      {a.status === "INACTIVE" && !a.active_version && manageable ? (
        <Alert>
          <AlertDescription>Publish a version before activating this agent.</AlertDescription>
        </Alert>
      ) : null}

      {editing ? (
        <Card>
          <CardHeader>
            <CardTitle>
              <h2>Edit draft</h2>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <AgentForm
              defaults={agentFormDefaults({
                name: a.name,
                category: a.category,
                description: a.description,
                configuration: a.draft_configuration,
              })}
              isOwner={isOwner}
              submitLabel="Save draft"
              pending={update.isPending}
              error={update.isError && !(update.error instanceof ApiError && update.error.isRevisionConflict) ? describeError(update.error) : null}
              onSubmit={(values) => update.mutate({ values, current: a })}
              onCancel={() => setEditing(false)}
            />
          </CardContent>
        </Card>
      ) : (
        <Tabs defaultValue="draft">
          <TabsList>
            <TabsTrigger value="draft">{a.has_unpublished_changes ? "Draft (unpublished)" : "Configuration"}</TabsTrigger>
            <TabsTrigger value="versions">Versions ({versions.data?.total ?? "…"})</TabsTrigger>
          </TabsList>
          <TabsContent value="draft" className="pt-2">
            <Card>
              <CardContent>
                <ConfigSummary configuration={a.draft_configuration} />
              </CardContent>
            </Card>
          </TabsContent>
          <TabsContent value="versions" className="pt-2">
            {versions.isPending ? (
              <ListSkeleton rows={2} label="Loading versions" />
            ) : versions.isError ? (
              <ErrorState error={versions.error} onRetry={() => void versions.refetch()} />
            ) : versions.data.items.length === 0 ? (
              <p className="text-sm text-muted-foreground">No published versions yet.</p>
            ) : (
              <ol className="space-y-3" aria-label="Published versions, newest first">
                {versions.data.items.map((v) => (
                  <li key={v.id}>
                    <details className="rounded-lg border bg-card p-4" open={v.version === a.active_version}>
                      <summary className="cursor-pointer text-sm font-medium">
                        Version {v.version}
                        {v.version === a.active_version ? " (active)" : ""} · published{" "}
                        <RelativeTime value={v.created_at} />
                      </summary>
                      <div className="mt-3">
                        <ConfigSummary configuration={v.configuration} />
                      </div>
                    </details>
                  </li>
                ))}
              </ol>
            )}
          </TabsContent>
        </Tabs>
      )}

      <ConfirmDialog
        open={confirmDeactivate}
        onOpenChange={setConfirmDeactivate}
        title="Deactivate this agent?"
        description="It can no longer be assigned to tasks until it is activated again."
        confirmLabel="Deactivate"
        destructive
        pending={command.isPending}
        onConfirm={() => command.mutate({ name: "deactivate", revision: a.revision })}
      />
      <RevisionConflictDialog
        open={errors.conflict}
        subject="agent"
        onReload={() => {
          errors.clearConflict();
          refresh();
        }}
      />
    </div>
  );
}
