"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { InboxIcon } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { PageHeader } from "@/components/common/page-header";
import { Pagination } from "@/components/common/pagination";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/common/states";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import type { InterventionStatus } from "@/lib/api/types";
import { useWorkspace } from "@/lib/workspace";

import { interventionKeys, RequestList } from "./request-list";

const PAGE_SIZE = 20;

/** The workspace's requests for people (ADR-0035); open ones first by default. */
export function RequestsView() {
  const workspace = useWorkspace();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const raw = params.get("status");
  // Open requests are the default view; "all" shows every status.
  const status: InterventionStatus | undefined =
    raw === "all" ? undefined : raw === "RESOLVED" || raw === "WITHDRAWN" || raw === "EXPIRED" ? raw : "OPEN";
  const offset = Math.max(0, Number.parseInt(params.get("offset") ?? "0", 10) || 0);

  const navigate = (next: { status: string; offset?: number }) => {
    const search = new URLSearchParams();
    if (next.status !== "OPEN") search.set("status", next.status);
    if (next.offset) search.set("offset", String(next.offset));
    const qs = search.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  const query = useQuery({
    queryKey: [...interventionKeys.all(workspace.id), "list", { status, offset, limit: PAGE_SIZE }],
    queryFn: () => api.interventions.list(workspace.id, { status, offset, limit: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Requests"
        description="Questions, approvals and escalated tasks that need a person. Answers reach the next plan draft."
      />
      <Tabs value={status ?? "all"} onValueChange={(v) => navigate({ status: v })}>
        <TabsList aria-label="Filter requests by status">
          <TabsTrigger value="OPEN">Open</TabsTrigger>
          <TabsTrigger value="RESOLVED">Resolved</TabsTrigger>
          <TabsTrigger value="WITHDRAWN">Withdrawn</TabsTrigger>
          <TabsTrigger value="EXPIRED">Expired</TabsTrigger>
          <TabsTrigger value="all">All</TabsTrigger>
        </TabsList>
      </Tabs>
      {query.isPending ? (
        <ListSkeleton label="Loading requests" />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.data.items.length === 0 ? (
        <EmptyState
          icon={<InboxIcon className="size-8" />}
          title={status === "OPEN" ? "Nothing needs your input" : "No requests"}
        />
      ) : (
        <>
          <RequestList items={query.data.items} showWorkflow />
          <Pagination
            offset={offset}
            limit={PAGE_SIZE}
            total={query.data.total}
            disabled={query.isFetching}
            onChange={(next) => navigate({ status: status ?? "all", offset: next })}
          />
        </>
      )}
    </div>
  );
}
