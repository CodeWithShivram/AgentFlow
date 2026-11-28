"use client";

import { useQueryClient } from "@tanstack/react-query";
import { SparklesIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { Button } from "@/components/ui/button";
import { workflowKeys } from "@/features/workflows/use-workflow";
import { api } from "@/lib/api";
import type { Workflow, WorkflowStatus, WorkspaceRole } from "@/lib/api/types";
import { useActionErrors } from "@/lib/hooks/use-action-errors";
import { roleAtLeast } from "@/lib/roles";
import { useWorkspace } from "@/lib/workspace";

/** Statuses from which "Build it for me" can take a project to RUNNING (ADR-0063). */
export const AUTOPILOT_STATUSES: ReadonlySet<WorkflowStatus> = new Set([
  "PENDING",
  "PLANNING",
  "AWAITING_APPROVAL",
  "READY",
]);

export type AutopilotStep =
  | { kind: "transition"; to: WorkflowStatus; label: string }
  | { kind: "confirm"; label: string }
  | { kind: "start"; label: string }
  | { kind: "wait"; label: string }
  | { kind: "done"; label: string }
  | { kind: "stop"; label: string };

/**
 * The next thing "Build it for me" does for a project in this state: the same steps a person takes
 * on Plan & controls, in order. It never decides anything a person must: it stops for questions,
 * failed planning, plan errors and, for non-admins, the approval.
 */
export function nextAutopilotStep(workflow: Workflow, role: WorkspaceRole): AutopilotStep {
  switch (workflow.status) {
    case "PENDING":
      return { kind: "transition", to: "PLANNING", label: "Starting planning…" };
    case "PLANNING":
      if (workflow.analysis_status === "FAILED" || workflow.plan_status === "FAILED") {
        return { kind: "stop", label: "Planning failed. Retry it from Plan & controls." };
      }
      if (workflow.confirmed_plan_version != null) {
        return { kind: "transition", to: "AWAITING_APPROVAL", label: "Sending the plan for approval…" };
      }
      if (workflow.plan_status === "READY") return { kind: "confirm", label: "Confirming the plan…" };
      if (workflow.analysis_status === "READY" && workflow.plan_status === "NOT_REQUESTED") {
        return { kind: "stop", label: "The planner has questions for you. Answer them under Requests." };
      }
      return { kind: "wait", label: "Planning…" };
    case "AWAITING_APPROVAL":
      return roleAtLeast(role, "ADMIN")
        ? { kind: "transition", to: "READY", label: "Approving the plan…" }
        : { kind: "stop", label: "Waiting for an admin to approve the plan." };
    case "READY":
      return { kind: "start", label: "Starting the build…" };
    default:
      return { kind: "done", label: "Building. Progress updates below." };
  }
}

const POLL_MS = 3000;

/**
 * Takes a new project from its prompt to a running build in one action (ADR-0063): plans,
 * confirms the latest draft (warnings accepted, errors stop), approves when the person is an admin,
 * and starts. It runs in this page; leaving stops it, and pressing it again continues.
 */
export function BuildForMe({ workflow }: { workflow: Workflow }) {
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const errors = useActionErrors();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const alive = useRef(true);
  useEffect(
    () => () => {
      alive.current = false;
    },
    [],
  );

  async function confirmLatestDraft(current: Workflow): Promise<"confirmed" | string> {
    const { items } = await api.workflows.planDrafts(workspace.id, current.id);
    const latest = items.reduce((a, b) => (b.version > a.version ? b : a), items[0]);
    if (!latest) return "No plan draft yet.";
    const preview = await api.workflows.planDraft(workspace.id, current.id, latest.version);
    if (preview.issues.some((issue) => issue.severity === "error")) {
      return "The plan has errors. Review it in Plan & controls.";
    }
    await api.workflows.confirmPlanDraft(workspace.id, current.id, latest.version, {
      revision: current.revision,
      acknowledge_warnings: true,
    });
    return "confirmed";
  }

  async function run() {
    setRunning(true);
    try {
      for (let guard = 0; guard < 400 && alive.current; guard++) {
        const current = await api.workflows.get(workspace.id, workflow.id);
        queryClient.setQueryData(workflowKeys.detail(workspace.id, workflow.id), current);
        const step = nextAutopilotStep(current, workspace.role);
        setStatus(step.label);
        if (step.kind === "done" || step.kind === "stop") break;
        if (step.kind === "wait") {
          await new Promise((resolve) => setTimeout(resolve, POLL_MS));
          continue;
        }
        if (step.kind === "transition") {
          await api.workflows.transition(workspace.id, current.id, { to_status: step.to, revision: current.revision });
        } else if (step.kind === "start") {
          await api.workflows.start(workspace.id, current.id, current.revision);
        } else {
          const outcome = await confirmLatestDraft(current);
          if (outcome !== "confirmed") {
            setStatus(outcome);
            break;
          }
        }
      }
    } catch (error) {
      setStatus(null);
      errors.handle(error);
    } finally {
      if (alive.current) setRunning(false);
      void queryClient.invalidateQueries({ queryKey: workflowKeys.detail(workspace.id, workflow.id) });
    }
  }

  return (
    <div className="space-y-2">
      <Button size="sm" onClick={() => setOpen(true)} disabled={running}>
        <SparklesIcon data-icon="inline-start" />
        {running ? "Building…" : "Build it for me"}
      </Button>
      {status ? (
        <p className="text-sm text-muted-foreground" role="status" aria-live="polite">
          {status}
        </p>
      ) : null}
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Build this project for you?"
        description="AgentFlow plans the project, confirms the plan (accepting its warnings; errors stop it), approves it if you are an admin, and starts the build. Keep this page open until the build starts."
        confirmLabel="Build it"
        onConfirm={() => {
          setOpen(false);
          void run();
        }}
      />
    </div>
  );
}
