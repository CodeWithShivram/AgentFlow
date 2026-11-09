"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { BotIcon, PlusIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { PageHeader } from "@/components/common/page-header";
import { Pagination } from "@/components/common/pagination";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/common/states";
import { StatusBadge } from "@/components/common/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import type { AgentStatus } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { workspacePath } from "@/lib/navigation";
import { useCan, useWorkspace } from "@/lib/workspace";

const PAGE_SIZE = 24;

export function AgentsListView() {
  const workspace = useWorkspace();
  const isAdmin = useCan("ADMIN");
  const isMember = useCan("MEMBER");
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const rawStatus = params.get("status");
  const status: AgentStatus | undefined = rawStatus === "ACTIVE" || rawStatus === "INACTIVE" ? rawStatus : undefined;
  const offset = Math.max(0, Number.parseInt(params.get("offset") ?? "0", 10) || 0);

  const query = useQuery({
    queryKey: ["agents", workspace.id, "list", { status, offset, limit: PAGE_SIZE }],
    queryFn: () => api.agents.list(workspace.id, { status, offset, limit: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });

  const navigate = (next: { status?: AgentStatus; offset?: number }) => {
    const search = new URLSearchParams();
    if (next.status) search.set("status", next.status);
    if (next.offset) search.set("offset", String(next.offset));
    const qs = search.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Agents"
        description="Specialists that execute tasks. Built-in agents are shared by every workspace."
        actions={
          <>
            {isMember && !isAdmin ? (
              <Button variant="outline" asChild>
                <Link href={workspacePath(workspace.id, "proposals?new=1")}>Propose an agent</Link>
              </Button>
            ) : null}
            {isAdmin ? (
              <Button asChild>
                <Link href={workspacePath(workspace.id, "agents/new")}>
                  <PlusIcon data-icon="inline-start" /> New agent
                </Link>
              </Button>
            ) : null}
          </>
        }
      />
      <Tabs value={status ?? "all"} onValueChange={(v) => navigate({ status: v === "all" ? undefined : (v as AgentStatus) })}>
        <TabsList aria-label="Filter agents by status">
          <TabsTrigger value="all">All</TabsTrigger>
          <TabsTrigger value="ACTIVE">Active</TabsTrigger>
          <TabsTrigger value="INACTIVE">Inactive</TabsTrigger>
        </TabsList>
      </Tabs>

      {query.isPending ? (
        <ListSkeleton label="Loading agents" />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.data.items.length === 0 ? (
        <EmptyState icon={<BotIcon className="size-8" />} title="No agents match" description="Try another filter." />
      ) : (
        <>
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-busy={query.isFetching}>
            {query.data.items.map((agent) => (
              <li key={agent.id}>
                <Link
                  href={workspacePath(workspace.id, `agents/${agent.id}`)}
                  className="block h-full rounded-lg border bg-card p-4 transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                >
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{agent.name}</span>
                    <StatusBadge status={agent.status} />
                    {agent.is_builtin ? <Badge variant="outline">Built-in</Badge> : null}
                  </span>
                  <span className="mt-1 block text-xs text-muted-foreground">
                    {humanize(agent.category)} ·{" "}
                    {agent.active_version ? `version ${agent.active_version}` : "not published"}
                    {agent.has_unpublished_changes && agent.active_version ? " · unpublished changes" : ""}
                  </span>
                  {agent.description ? (
                    <span className="mt-2 line-clamp-2 block text-sm text-muted-foreground">{agent.description}</span>
                  ) : null}
                </Link>
              </li>
            ))}
          </ul>
          <Pagination
            offset={offset}
            limit={PAGE_SIZE}
            total={query.data.total}
            disabled={query.isFetching}
            onChange={(next) => navigate({ status, offset: next })}
          />
        </>
      )}
    </div>
  );
}
