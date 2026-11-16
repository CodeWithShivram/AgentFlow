"use client";

import { PauseCircleIcon } from "lucide-react";

import { RelativeTime } from "@/components/common/relative-time";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import type { PauseReason, Workflow } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/auth-provider";

const TITLES: Record<PauseReason, string> = {
  USER: "Paused by a member",
  DEADLINE_PASSED: "Paused at its deadline",
  AWAITING_DECISION: "Paused for a decision",
};

const EXPLANATIONS: Record<PauseReason, string> = {
  USER: "Nothing new is dispatched until someone resumes it; running attempts finish.",
  DEADLINE_PASSED:
    "The deadline passed. Resume to carry on past it (it does not pause again), replan the remaining work, or cancel.",
  AWAITING_DECISION:
    "An agent asked for approval and this workflow's plan needs approval, so it waits for an admin. It resumes by itself once every such request is decided. Resuming now carries on with other tasks while the request stays open.",
};

/** Why a paused workflow is paused, by whom and since when (ADR-0036). */
export function PauseBanner({ workflow }: { workflow: Workflow }) {
  const { me } = useAuth();
  const reason = workflow.pause_reason;
  if (workflow.status !== "PAUSED" || !reason) return null;
  const byMe = reason === "USER" && workflow.paused_by_user_id === me?.user.id;
  return (
    <Alert role="region" aria-label="Paused">
      <PauseCircleIcon aria-hidden />
      <AlertTitle>
        {byMe ? "Paused by you" : TITLES[reason]}
        {workflow.paused_at ? (
          <span className="font-normal text-muted-foreground">
            {" "}
            · <RelativeTime value={workflow.paused_at} />
          </span>
        ) : null}
      </AlertTitle>
      <AlertDescription>
        {reason === "USER" && workflow.pause_note ? (
          <p className="whitespace-pre-wrap break-words">
            <span className="font-medium text-foreground">Reason: </span>
            {workflow.pause_note}
          </p>
        ) : null}
        <p>{EXPLANATIONS[reason]}</p>
        <p>Resuming dispatches waiting tasks by priority, within the parallel task limit.</p>
      </AlertDescription>
    </Alert>
  );
}
