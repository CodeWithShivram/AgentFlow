"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { PageHeader } from "@/components/common/page-header";
import { EmptyState } from "@/components/common/states";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api";
import { describeError } from "@/lib/api/errors";
import { workspacePath } from "@/lib/navigation";
import { useCan, useWorkspace } from "@/lib/workspace";

import { agentFormDefaults, type AgentFormValues, toConfiguration } from "./agent-config-fields";
import { AgentForm } from "./agent-form";

export function AgentCreateView() {
  const workspace = useWorkspace();
  const isAdmin = useCan("ADMIN");
  const isOwner = useCan("OWNER");
  const router = useRouter();
  const queryClient = useQueryClient();

  const create = useMutation({
    mutationFn: (values: AgentFormValues) =>
      api.agents.create(workspace.id, {
        name: values.name,
        category: values.category,
        description: values.description,
        configuration: toConfiguration(values),
      }),
    onSuccess: (agent) => {
      void queryClient.invalidateQueries({ queryKey: ["agents", workspace.id] });
      toast.success("Agent created as a draft. Publish it to use it.");
      router.push(workspacePath(workspace.id, `agents/${agent.id}`));
    },
  });

  if (!isAdmin) {
    return (
      <EmptyState
        title="Admins create agents"
        description="Members can propose an agent for an admin to review on the Agent proposals page."
      />
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader title="New agent" description="Agents start inactive with an unpublished draft." />
      <Card>
        <CardContent>
          <AgentForm
            defaults={agentFormDefaults()}
            isOwner={isOwner}
            submitLabel="Create agent"
            pending={create.isPending}
            error={create.isError ? describeError(create.error) : null}
            onSubmit={(values) => create.mutate(values)}
            onCancel={() => router.push(workspacePath(workspace.id, "agents"))}
          />
        </CardContent>
      </Card>
    </div>
  );
}
