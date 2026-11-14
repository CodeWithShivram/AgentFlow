"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardCheckIcon, PlusIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { FormError } from "@/components/common/form-field";
import { PageHeader } from "@/components/common/page-header";
import { Pagination } from "@/components/common/pagination";
import { RelativeTime } from "@/components/common/relative-time";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/common/states";
import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { describeError } from "@/lib/api/errors";
import type { Proposal, ProposalStatus } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/auth-provider";
import { humanize } from "@/lib/format";
import { workspacePath } from "@/lib/navigation";
import { useCan, useWorkspace } from "@/lib/workspace";

import { agentFormDefaults, type AgentFormValues, toConfiguration } from "./agent-config-fields";
import { AgentForm } from "./agent-form";
import { ConfigSummary } from "./config-summary";
import { PermissionReview } from "./permission-review";

const PAGE_SIZE = 20;
type Decision = "approve" | "reject";

export function ProposalsView() {
  const workspace = useWorkspace();
  const { me } = useAuth();
  const isMember = useCan("MEMBER");
  const isAdmin = useCan("ADMIN");
  const isOwner = useCan("OWNER");
  const queryClient = useQueryClient();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const raw = params.get("status");
  const status: ProposalStatus | undefined =
    raw === "PENDING" || raw === "APPROVED" || raw === "REJECTED" ? raw : undefined;
  const offset = Math.max(0, Number.parseInt(params.get("offset") ?? "0", 10) || 0);
  const creating = params.get("new") === "1" && isMember;
  const [deciding, setDeciding] = useState<{ proposal: Proposal; decision: Decision } | null>(null);
  const [note, setNote] = useState("");

  const navigate = (next: { status?: ProposalStatus; offset?: number; create?: boolean }) => {
    const search = new URLSearchParams();
    if (next.status) search.set("status", next.status);
    if (next.offset) search.set("offset", String(next.offset));
    if (next.create) search.set("new", "1");
    const qs = search.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  const query = useQuery({
    queryKey: ["proposals", workspace.id, "list", { status, offset, limit: PAGE_SIZE }],
    queryFn: () => api.proposals.list(workspace.id, { status, offset, limit: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });

  const submit = useMutation({
    mutationFn: (values: AgentFormValues) =>
      api.proposals.submit(workspace.id, {
        name: values.name,
        category: values.category,
        description: values.description,
        rationale: values.rationale,
        configuration: toConfiguration(values),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["proposals", workspace.id] });
      toast.success("Proposal submitted for review");
      navigate({ status: "PENDING" });
    },
  });

  const decide = useMutation({
    mutationFn: ({ proposal, decision }: { proposal: Proposal; decision: Decision }) =>
      decision === "approve"
        ? api.proposals.approve(workspace.id, proposal.id, note)
        : api.proposals.reject(workspace.id, proposal.id, note),
    onSuccess: (result, { decision }) => {
      void queryClient.invalidateQueries({ queryKey: ["proposals", workspace.id] });
      void queryClient.invalidateQueries({ queryKey: ["agents", workspace.id] });
      setDeciding(null);
      setNote("");
      if (decision === "approve" && result.agent_id) {
        toast.success("Approved. The agent is registered as inactive; activate it when ready.", {
          action: {
            label: "Open agent",
            onClick: () => router.push(workspacePath(workspace.id, `agents/${result.agent_id}`)),
          },
        });
      } else {
        toast.success(decision === "approve" ? "Proposal approved" : "Proposal rejected");
      }
    },
  });

  if (creating) {
    return (
      <div className="space-y-6">
        <PageHeader title="Propose an agent" description="An admin reviews the proposal. You cannot review your own." />
        <Card>
          <CardContent>
            <AgentForm
              proposal
              defaults={agentFormDefaults()}
              isOwner={isOwner}
              submitLabel="Submit proposal"
              pending={submit.isPending}
              error={submit.isError ? describeError(submit.error) : null}
              onSubmit={(values) => submit.mutate(values)}
              onCancel={() => navigate({ status })}
            />
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Agent proposals"
        description="Requests for new agents. Approved proposals become inactive agents with version 1."
        actions={
          isMember ? (
            <Button onClick={() => navigate({ create: true })}>
              <PlusIcon data-icon="inline-start" /> Propose an agent
            </Button>
          ) : null
        }
      />
      <Tabs
        value={status ?? "all"}
        onValueChange={(v) => navigate({ status: v === "all" ? undefined : (v as ProposalStatus) })}
      >
        <TabsList aria-label="Filter proposals by status">
          <TabsTrigger value="all">All</TabsTrigger>
          <TabsTrigger value="PENDING">Pending</TabsTrigger>
          <TabsTrigger value="APPROVED">Approved</TabsTrigger>
          <TabsTrigger value="REJECTED">Rejected</TabsTrigger>
        </TabsList>
      </Tabs>

      {query.isPending ? (
        <ListSkeleton label="Loading proposals" />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.data.items.length === 0 ? (
        <EmptyState icon={<ClipboardCheckIcon className="size-8" />} title="No proposals" />
      ) : (
        <>
          <ul className="space-y-3">
            {query.data.items.map((p) => {
              const own = p.requested_by === me?.user.id;
              return (
                <li key={p.id}>
                  <Card>
                    <CardHeader>
                      <CardTitle className="flex flex-wrap items-center gap-2">
                        <h2 className="text-base">{p.name}</h2>
                        <StatusBadge status={p.status} />
                        <span className="text-xs font-normal text-muted-foreground">
                          {humanize(p.category)} · submitted <RelativeTime value={p.created_at} />
                          {own ? " by you" : p.requested_by === null ? " by the platform" : ""}
                        </span>
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <p className="text-sm whitespace-pre-wrap">
                        <span className="font-medium">Rationale: </span>
                        {p.rationale}
                      </p>
                      {p.source_workflow_id ? (
                        <p className="text-sm">
                          A task in{" "}
                          <Link className="underline" href={workspacePath(workspace.id, `workflows/${p.source_workflow_id}`)}>
                            this workflow
                          </Link>{" "}
                          had no suitable agent.
                        </p>
                      ) : null}
                      <PermissionReview configuration={p.proposed_configuration} isOwner={isOwner} />
                      <details>
                        <summary className="cursor-pointer text-sm text-muted-foreground">Proposed configuration</summary>
                        <div className="mt-2">
                          <ConfigSummary configuration={p.proposed_configuration} />
                        </div>
                      </details>
                      {p.review_note ? (
                        <p className="text-sm text-muted-foreground">Review note: {p.review_note}</p>
                      ) : null}
                      {p.agent_id ? (
                        <Button variant="link" className="px-0" asChild>
                          <Link href={workspacePath(workspace.id, `agents/${p.agent_id}`)}>Open registered agent</Link>
                        </Button>
                      ) : null}
                      {isAdmin && p.status === "PENDING" ? (
                        own ? (
                          <p className="text-sm text-muted-foreground">Another admin must review your own proposal.</p>
                        ) : (
                          <div className="flex gap-2">
                            <Button onClick={() => setDeciding({ proposal: p, decision: "approve" })}>Approve</Button>
                            <Button variant="outline" onClick={() => setDeciding({ proposal: p, decision: "reject" })}>
                              Reject
                            </Button>
                          </div>
                        )
                      ) : null}
                    </CardContent>
                  </Card>
                </li>
              );
            })}
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

      <Dialog
        open={deciding !== null}
        onOpenChange={(open) => {
          if (decide.isPending || open) return;
          setDeciding(null);
          decide.reset();
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{deciding?.decision === "approve" ? "Approve proposal" : "Reject proposal"}</DialogTitle>
            <DialogDescription>
              {deciding?.decision === "approve"
                ? `"${deciding.proposal.name}" is registered as an inactive agent with version 1.`
                : `"${deciding?.proposal.name ?? ""}" is rejected. The proposer can see your note.`}
            </DialogDescription>
          </DialogHeader>
          <FormError message={decide.isError ? describeError(decide.error) : null} />
          <div className="grid gap-1.5">
            <Label htmlFor="decision-note">Note (optional)</Label>
            <Textarea id="decision-note" rows={3} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={decide.isPending} onClick={() => setDeciding(null)}>
              Cancel
            </Button>
            <Button
              variant={deciding?.decision === "reject" ? "destructive" : "default"}
              disabled={decide.isPending}
              onClick={() => deciding && decide.mutate(deciding)}
            >
              {decide.isPending ? "Saving…" : deciding?.decision === "approve" ? "Approve" : "Reject"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
