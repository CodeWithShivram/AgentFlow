"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ShieldCheckIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { PageHeader } from "@/components/common/page-header";
import { Pagination } from "@/components/common/pagination";
import { RelativeTime } from "@/components/common/relative-time";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/common/states";
import { api } from "@/lib/api";
import { workspacePath } from "@/lib/navigation";
import { useCan, useWorkspace } from "@/lib/workspace";

import { AuditLine, interventionKeys, SUBJECT_LABELS } from "./request-list";

const PAGE_SIZE = 20;

/**
 * The workspace's approval decisions (ADMIN, ADR-0038): what was decided, by whom, in which
 * role, and the fingerprint of exactly what they were shown. Records are immutable.
 */
export function ApprovalsView() {
  const workspace = useWorkspace();
  const isAdmin = useCan("ADMIN");
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const offset = Math.max(0, Number.parseInt(params.get("offset") ?? "0", 10) || 0);

  const query = useQuery({
    queryKey: [...interventionKeys.all(workspace.id), "approvals", { offset, limit: PAGE_SIZE }],
    queryFn: () => api.approvals.list(workspace.id, { offset, limit: PAGE_SIZE }),
    placeholderData: keepPreviousData,
    enabled: isAdmin,
  });

  const header = (
    <PageHeader
      title="Approval history"
      description="Every decision on an approval request, with who decided, in which role, and a fingerprint of what they approved. Records cannot be changed."
    />
  );
  if (!isAdmin) {
    return (
      <div className="space-y-6">
        {header}
        <EmptyState title="Admins only" description="Only admins and owners can see the approval history." />
      </div>
    );
  }
  return (
    <div className="space-y-6">
      {header}
      {query.isPending ? (
        <ListSkeleton label="Loading approval history" />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.data.items.length === 0 ? (
        <EmptyState icon={<ShieldCheckIcon className="size-8" />} title="No decisions yet" />
      ) : (
        <>
          <ul className="space-y-3">
            {query.data.items.map((item) => (
              <li key={item.id}>
                <article aria-label={SUBJECT_LABELS[item.subject]} className="space-y-2 rounded-lg border p-4 text-sm">
                  <header className="flex flex-wrap items-center gap-2">
                    <h2 className="font-medium">
                      {item.expired ? "Expired" : item.decision === "approve" ? "Approved" : "Rejected"}
                      {item.then === "stop" ? " and stopped" : ""}: {SUBJECT_LABELS[item.subject]}
                    </h2>
                    <span className="text-xs text-muted-foreground">
                      <RelativeTime value={item.decided_at} />
                    </span>
                  </header>
                  <p className="whitespace-pre-wrap break-words">{item.requested_action}</p>
                  {item.note ? (
                    <p className="whitespace-pre-wrap break-words text-muted-foreground">
                      <span className="font-medium text-foreground">Note: </span>
                      {item.note}
                    </p>
                  ) : null}
                  <div className="text-muted-foreground">
                    <AuditLine record={item} />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Workflow:{" "}
                    <Link className="underline" href={workspacePath(workspace.id, `workflows/${item.workflow_id}`)}>
                      {item.workflow_prompt || "Open workflow"}
                    </Link>
                  </p>
                </article>
              </li>
            ))}
          </ul>
          <Pagination
            offset={offset}
            limit={PAGE_SIZE}
            total={query.data.total}
            disabled={query.isFetching}
            onChange={(next) => router.replace(next ? `${pathname}?offset=${next}` : pathname, { scroll: false })}
          />
        </>
      )}
    </div>
  );
}
