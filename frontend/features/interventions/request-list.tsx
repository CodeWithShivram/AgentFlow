"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { FormError } from "@/components/common/form-field";
import { RelativeTime } from "@/components/common/relative-time";
import { StatusBadge } from "@/components/common/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { describeError } from "@/lib/api/errors";
import type { ApprovalRecord, Intervention, InterventionSubject } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { workspacePath } from "@/lib/navigation";
import { useCan, useWorkspace } from "@/lib/workspace";

export const interventionKeys = {
  all: (workspaceId: string) => ["interventions", workspaceId] as const,
};

export const SUBJECT_LABELS: Record<InterventionSubject, string> = {
  analysis_question: "Question about the request",
  plan_approval: "Plan approval",
  agent_action: "Agent asks for approval",
  task_escalation: "Escalated task",
};

const MAX_ANSWER = 4000;

function texts(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/**
 * Requests that need a person (ADR-0035). Members answer questions; admins approve or reject;
 * escalated tasks are resolved by acting on the task. Requested actions and contexts may be
 * model or agent text, rendered as plain text only.
 */
export function RequestList({ items, showWorkflow }: { items: Intervention[]; showWorkflow: boolean }) {
  return (
    <ul className="space-y-3">
      {items.map((item) => (
        <li key={item.id}>
          <RequestItem item={item} showWorkflow={showWorkflow} />
        </li>
      ))}
    </ul>
  );
}

function RequestItem({ item, showWorkflow }: { item: Intervention; showWorkflow: boolean }) {
  const workspace = useWorkspace();
  const workflowHref = workspacePath(workspace.id, `workflows/${item.workflow_id}`);
  const open = item.status === "OPEN";
  return (
    <article
      aria-label={SUBJECT_LABELS[item.subject]}
      className="space-y-3 rounded-lg border p-4 text-sm"
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="font-medium">{SUBJECT_LABELS[item.subject]}</h3>
        <StatusBadge status={item.status} />
        <Badge variant="outline">{humanize(item.type)}</Badge>
        <span className="text-xs text-muted-foreground">
          <RelativeTime value={item.created_at} />
          {open && item.expires_at ? (
            <>
              {" "}
              · expires <RelativeTime value={item.expires_at} />
            </>
          ) : null}
        </span>
      </header>
      {showWorkflow ? (
        <p className="text-xs text-muted-foreground">
          Workflow:{" "}
          <Link className="underline" href={workflowHref}>
            {item.workflow_prompt || "Open workflow"}
          </Link>
        </p>
      ) : null}
      <p className="whitespace-pre-wrap break-words">{item.requested_action}</p>
      <Context item={item} />
      {open ? <Actions item={item} workflowHref={workflowHref} /> : <Resolution item={item} />}
    </article>
  );
}

function Context({ item }: { item: Intervention }) {
  const c = item.context;
  if (item.subject === "analysis_question") {
    const impact = text(c.impact);
    return impact ? <p className="text-muted-foreground">Why it matters: {impact}</p> : null;
  }
  if (item.subject === "agent_action") {
    const reason = text(c.reason);
    const risks = texts(c.risks);
    const consequences = texts(c.consequences);
    return (
      <dl className="space-y-2">
        {reason ? (
          <div>
            <dt className="font-medium">Reason given by the agent</dt>
            <dd className="whitespace-pre-wrap break-words">{reason}</dd>
          </div>
        ) : null}
        {risks.length > 0 ? (
          <div>
            <dt className="font-medium">Risks</dt>
            <dd>
              <ul className="list-disc pl-5">
                {risks.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </dd>
          </div>
        ) : null}
        {consequences.length > 0 ? (
          <div>
            <dt className="font-medium">Consequences</dt>
            <dd>
              <ul className="list-disc pl-5">
                {consequences.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </dd>
          </div>
        ) : null}
        <div className="text-muted-foreground">
          <p>If approved: {text(c.if_approved)}</p>
          <p>If rejected: {text(c.if_rejected)}</p>
          {text(c.if_expired) ? <p>If nobody decides in time: {text(c.if_expired)}</p> : null}
        </div>
      </dl>
    );
  }
  if (item.subject === "task_escalation") {
    const reason = text(c.reason);
    return reason ? <p className="text-muted-foreground">Reason: {humanize(reason)}</p> : null;
  }
  return null;
}

function decisionText(decision: string, then: string | null): string {
  if (decision === "approve") return "Approved";
  return then === "stop" ? "Rejected; the task was stopped for people to decide" : "Rejected";
}

function Resolution({ item }: { item: Intervention }) {
  const r = item.resolution ?? {};
  const answer = text(r.answer);
  const decision = text(r.decision);
  const outcome = text(r.outcome);
  const note = text(r.note);
  return (
    <div className="space-y-1 text-muted-foreground">
      <p>
        {answer ? (
          <>
            <span className="font-medium text-foreground">Answer: </span>
            <span className="whitespace-pre-wrap break-words">{answer}</span>
          </>
        ) : item.status === "EXPIRED" ? (
          "Expired: nobody decided in time, so it was handled as a rejection"
        ) : decision ? (
          decisionText(decision, text(r.then))
        ) : item.status === "WITHDRAWN" ? (
        `Withdrawn${outcome ? `: ${humanize(outcome)}` : ""}`
        ) : (
          `Resolved${outcome ? `: ${humanize(outcome)}` : ""}`
        )}
        {item.resolved_at ? (
          <>
            {" "}
            · <RelativeTime value={item.resolved_at} />
          </>
        ) : null}
      </p>
      {note ? (
        <p className="whitespace-pre-wrap break-words">
          <span className="font-medium text-foreground">Note: </span>
          {note}
        </p>
      ) : null}
      {item.decision_record ? <AuditLine record={item.decision_record} /> : null}
    </div>
  );
}

/** Who decided, in which role, and the fingerprint of what they were shown (ADR-0038). */
export function AuditLine({ record }: { record: ApprovalRecord }) {
  const who = record.expired
    ? "Expired (no person decided)"
    : `Decided as ${humanize(record.decided_role ?? "")}${record.by_workflow_creator ? " by the workflow's creator" : ""}`;
  return (
    <p className="text-xs">
      {who} · needed {humanize(record.required_role)} · fingerprint{" "}
      <code title={record.action_fingerprint}>{record.action_fingerprint.slice(0, 12)}…</code>
    </p>
  );
}

function useRefresh(workflowId: string) {
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: interventionKeys.all(workspace.id) });
    void queryClient.invalidateQueries({ queryKey: ["workflows", workspace.id, "detail", workflowId] });
  };
}

function Actions({ item, workflowHref }: { item: Intervention; workflowHref: string }) {
  if (item.type === "CLARIFICATION") return <AnswerForm item={item} />;
  if (item.type === "APPROVAL") return <Decision item={item} />;
  return (
    <p className="text-muted-foreground">
      Resolved when someone reassigns, cancels or replans the task on{" "}
      <Link className="underline" href={workflowHref}>
        the workflow page
      </Link>
      .
    </p>
  );
}

function AnswerForm({ item }: { item: Intervention }) {
  const workspace = useWorkspace();
  const canAnswer = useCan("MEMBER");
  const refresh = useRefresh(item.workflow_id);
  const [answer, setAnswer] = useState("");
  const send = useMutation({
    mutationFn: () => api.interventions.answer(workspace.id, item.id, answer.trim()),
    onSuccess: () => {
      toast.success("Answer saved. It reaches the next plan draft.");
      setAnswer("");
      refresh();
    },
  });
  if (!canAnswer) return <p className="text-muted-foreground">A member can answer this question.</p>;
  const id = `answer-${item.id}`;
  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (answer.trim()) send.mutate();
      }}
    >
      <FormError message={send.isError ? describeError(send.error) : null} />
      <Label htmlFor={id}>Your answer</Label>
      <Textarea
        id={id}
        rows={3}
        maxLength={MAX_ANSWER}
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
      />
      <Button type="submit" size="sm" disabled={send.isPending || !answer.trim()}>
        {send.isPending ? "Saving…" : "Send answer"}
      </Button>
    </form>
  );
}

type Then = "retry" | "stop";

const THEN_CHOICES: readonly (readonly [Then, string, string])[] = [
  ["retry", "Finish without it", "The same agent makes a new attempt without that action."],
  ["stop", "Stop the task", "The task is escalated for people to reassign, cancel or replan."],
];

function Decision({ item }: { item: Intervention }) {
  const workspace = useWorkspace();
  // Per-action authorization (ADR-0038): the request names the least role that may decide it.
  const required = item.required_role ?? "ADMIN";
  const canDecide = useCan(required);
  const refresh = useRefresh(item.workflow_id);
  const [rejecting, setRejecting] = useState(false);
  const [then, setThen] = useState<Then>("retry");
  const [note, setNote] = useState("");
  const agentAction = item.subject === "agent_action";
  const decide = useMutation({
    mutationFn: (decision: "approve" | "reject") => {
      if (decision === "approve") return api.interventions.approve(workspace.id, item.id);
      const trimmed = note.trim();
      return api.interventions.reject(workspace.id, item.id, {
        ...(agentAction ? { then } : {}),
        ...(trimmed ? { note: trimmed } : {}),
      });
    },
    onSuccess: (_, decision) => {
      toast.success(decision === "approve" ? "Approved" : "Rejected");
      setRejecting(false);
      refresh();
    },
    onError: (error) => {
      toast.error(describeError(error));
      setRejecting(false);
      refresh();
    },
  });
  if (!canDecide) {
    return (
      <p className="text-muted-foreground">
        {required === "OWNER"
          ? "An owner approves or rejects this request: the agent holds high-risk tools."
          : "An admin approves or rejects this request."}
      </p>
    );
  }
  const noteId = `reject-note-${item.id}`;
  return (
    <>
      <div className="flex gap-2">
        <Button size="sm" disabled={decide.isPending} onClick={() => decide.mutate("approve")}>
          Approve
        </Button>
        <Button size="sm" variant="outline" disabled={decide.isPending} onClick={() => setRejecting(true)}>
          Reject…
        </Button>
      </div>
      <Dialog open={rejecting} onOpenChange={(next) => !decide.isPending && setRejecting(next)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject this request</DialogTitle>
            <DialogDescription>
              {agentAction
                ? "The action is not taken. Choose what happens to the task."
                : "The plan goes back to planning."}
            </DialogDescription>
          </DialogHeader>
          {agentAction ? (
            <fieldset className="grid gap-2">
              <legend className="mb-1 text-sm font-medium">Then</legend>
              {THEN_CHOICES.map(([value, label, hint]) => (
                <label key={value} className="flex items-start gap-2 text-sm">
                  <input
                    type="radio"
                    name={`then-${item.id}`}
                    value={value}
                    checked={then === value}
                    onChange={() => setThen(value)}
                    className="mt-1"
                  />
                  <span>
                    <span className="font-medium">{label}</span>
                    <span className="block text-muted-foreground">{hint}</span>
                  </span>
                </label>
              ))}
            </fieldset>
          ) : null}
          <div className="grid gap-1.5">
            <Label htmlFor={noteId}>Note for people (optional)</Label>
            <Textarea id={noteId} rows={3} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
            <p className="text-xs text-muted-foreground">Shown on the request; never sent to agents.</p>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={decide.isPending} onClick={() => setRejecting(false)}>
              Cancel
            </Button>
            <Button variant="destructive" disabled={decide.isPending} onClick={() => decide.mutate("reject")}>
              {decide.isPending ? "Saving…" : "Reject"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
