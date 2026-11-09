"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { PlusIcon, WorkflowIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { PageHeader } from "@/components/common/page-header";
import { Pagination } from "@/components/common/pagination";
import { RelativeTime } from "@/components/common/relative-time";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/common/states";
import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api";
import { WORKFLOW_STATUSES, type WorkflowStatus } from "@/lib/api/types";
import { formatMoney, humanize } from "@/lib/format";
import { workspacePath } from "@/lib/navigation";
import { useCan, useWorkspace } from "@/lib/workspace";

const PAGE_SIZE = 20;
const ALL = "all";

function parseStatus(value: string | null): WorkflowStatus | undefined {
  return WORKFLOW_STATUSES.find((s) => s === value);
}

function parseOffset(value: string | null): number {
  const n = Number(value);
  return Number.isInteger(n) && n >= 0 && n <= 100_000 ? n : 0;
}

export function WorkflowsListView() {
  const workspace = useWorkspace();
  const canCreate = useCan("MEMBER");
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const status = parseStatus(params.get("status"));
  const offset = parseOffset(params.get("offset"));

  const query = useQuery({
    queryKey: ["workflows", workspace.id, "list", { status, offset, limit: PAGE_SIZE }],
    queryFn: () => api.workflows.list(workspace.id, { status, offset, limit: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });

  function setFilters(next: { status?: WorkflowStatus; offset?: number }) {
    const search = new URLSearchParams();
    if (next.status) search.set("status", next.status);
    if (next.offset) search.set("offset", String(next.offset));
    const qs = search.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  const newButton = canCreate ? (
    <Button asChild>
      <Link href={workspacePath(workspace.id, "workflows/new")}>
        <PlusIcon data-icon="inline-start" />
        New workflow
      </Link>
    </Button>
  ) : null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Workflows"
        description="Goals broken into tasks that agents execute."
        actions={newButton}
      />

      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="status-filter" className="text-sm font-medium">
          Status
        </label>
        <Select
          value={status ?? ALL}
          onValueChange={(value) => setFilters({ status: parseStatus(value) })}
        >
          <SelectTrigger id="status-filter" className="w-52">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All statuses</SelectItem>
            {WORKFLOW_STATUSES.map((s) => (
              <SelectItem key={s} value={s}>
                {humanize(s)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {query.isPending ? (
        <ListSkeleton label="Loading workflows" />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.data.items.length === 0 ? (
        <EmptyState
          icon={<WorkflowIcon className="size-8" />}
          title={status ? `No ${humanize(status).toLowerCase()} workflows` : "No workflows yet"}
          description={
            status
              ? "Try another status filter."
              : canCreate
                ? "Describe a goal and plan the tasks that achieve it."
                : "Members of this workspace can create workflows."
          }
          action={status ? null : newButton}
        />
      ) : (
        <>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Goal</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="hidden md:table-cell">Budget limit</TableHead>
                  <TableHead className="hidden md:table-cell">Policy</TableHead>
                  <TableHead className="hidden sm:table-cell">Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody aria-busy={query.isFetching}>
                {query.data.items.map((w) => (
                  <TableRow key={w.id}>
                    <TableCell className="max-w-[16rem] whitespace-normal sm:max-w-md">
                      <Link
                        href={workspacePath(workspace.id, `workflows/${w.id}`)}
                        className="line-clamp-2 font-medium hover:underline"
                      >
                        {w.prompt}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={w.status} />
                    </TableCell>
                    <TableCell className="hidden tabular-nums md:table-cell">
                      {formatMoney(w.budget_limit, w.currency)}
                    </TableCell>
                    <TableCell className="hidden md:table-cell">
                      {w.autonomy_policy === "autonomous" ? "Autonomous" : "Approval required"}
                    </TableCell>
                    <TableCell className="hidden sm:table-cell">
                      <RelativeTime value={w.created_at} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <Pagination
            offset={offset}
            limit={PAGE_SIZE}
            total={query.data.total}
            disabled={query.isFetching}
            onChange={(next) => setFilters({ status, offset: next })}
          />
        </>
      )}
    </div>
  );
}
