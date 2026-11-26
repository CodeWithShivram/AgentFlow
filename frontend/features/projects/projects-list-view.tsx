"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { PackageIcon, PlusIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { PageHeader } from "@/components/common/page-header";
import { Pagination } from "@/components/common/pagination";
import { RelativeTime } from "@/components/common/relative-time";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/common/states";
import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api";
import { workspacePath } from "@/lib/navigation";
import { useCan, useWorkspace } from "@/lib/workspace";

import { projectName } from "./phase";

const PAGE_SIZE = 20;

function parseOffset(value: string | null): number {
  const n = Number(value);
  return Number.isInteger(n) && n >= 0 && n <= 100_000 ? n : 0;
}

/** Software projects: workflows of kind SOFTWARE_PROJECT (ADR-0053). */
export function ProjectsListView() {
  const workspace = useWorkspace();
  const canCreate = useCan("MEMBER");
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const offset = parseOffset(params.get("offset"));

  const query = useQuery({
    queryKey: ["workflows", workspace.id, "list", { kind: "SOFTWARE_PROJECT", offset, limit: PAGE_SIZE }],
    queryFn: () => api.workflows.list(workspace.id, { kind: "SOFTWARE_PROJECT", offset, limit: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });

  const setOffset = (next: number) => {
    router.replace(next ? `${pathname}?offset=${next}` : pathname, { scroll: false });
  };

  const newButton = canCreate ? (
    <Button asChild>
      <Link href={workspacePath(workspace.id, "projects/new")}>
        <PlusIcon data-icon="inline-start" />
        New project
      </Link>
    </Button>
  ) : null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Projects"
        description="Describe software, approve the plan, watch it being built and download a ZIP."
        actions={newButton}
      />

      {query.isPending ? (
        <ListSkeleton label="Loading projects" />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.data.items.length === 0 ? (
        <EmptyState
          icon={<PackageIcon className="size-8" />}
          title="No projects yet"
          description={
            canCreate
              ? "Describe the software you want. AgentFlow plans it, builds it with agents and packages it."
              : "Members of this workspace can create projects."
          }
          action={newButton}
        />
      ) : (
        <>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Project</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="hidden sm:table-cell">Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody aria-busy={query.isFetching}>
                {query.data.items.map((w) => (
                  <TableRow key={w.id}>
                    <TableCell className="max-w-[16rem] whitespace-normal sm:max-w-md">
                      <Link
                        href={workspacePath(workspace.id, `projects/${w.id}`)}
                        className="line-clamp-2 font-medium break-words hover:underline"
                      >
                        {projectName(w)}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={w.status} />
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
            onChange={setOffset}
          />
        </>
      )}
    </div>
  );
}
