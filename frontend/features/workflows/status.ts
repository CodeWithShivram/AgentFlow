// Workflow and task status rules the UI needs, mirrored from the backend's state machine
// (app/workflows/state_machine.py) and app/services/workflows.py. The API remains the authority;
// these only decide which actions to offer.

import type { AutonomyPolicy, TaskStatus, WorkflowStatus, WorkspaceRole } from "@/lib/api/types";
import { roleAtLeast } from "@/lib/roles";

export const WORKFLOW_TERMINAL: ReadonlySet<WorkflowStatus> = new Set([
  "COMPLETED",
  "FAILED",
  "CANCELLED",
]);
export const TASK_TERMINAL: ReadonlySet<TaskStatus> = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

/** Plan (tasks, dependencies) is editable; editing in the last two reopens PLANNING. */
export const PLAN_EDITABLE: ReadonlySet<WorkflowStatus> = new Set([
  "PENDING",
  "PLANNING",
  "AWAITING_APPROVAL",
  "READY",
]);
export const PLAN_EDIT_REOPENS: ReadonlySet<WorkflowStatus> = new Set(["AWAITING_APPROVAL", "READY"]);

/** Statuses in which work may still change on its own, so the page keeps polling. */
export const WORKFLOW_ACTIVE: ReadonlySet<WorkflowStatus> = new Set(["RUNNING", "PAUSED", "BLOCKED"]);
export const TASK_IN_FLIGHT: ReadonlySet<TaskStatus> = new Set([
  "ASSIGNED",
  "QUEUED",
  "IN_PROGRESS",
  "VERIFYING",
]);

/** Task statuses from which a user may assign or reassign an agent. */
export const TASK_ASSIGNABLE: ReadonlySet<TaskStatus> = new Set([
  "READY",
  "REWORK_REQUIRED",
  "ESCALATED",
]);

/** The workflow must be executing for a task to be cancelled; during planning, remove it. */
export const WORKFLOW_EXECUTING: ReadonlySet<WorkflowStatus> = new Set(["RUNNING", "PAUSED", "BLOCKED"]);

export type WorkflowAction =
  | "plan"
  | "submit_for_approval"
  | "mark_ready"
  | "approve"
  | "request_changes"
  | "start"
  | "reopen"
  | "pause"
  | "resume"
  | "unblock"
  | "pause_blocked"
  | "cancel";

export interface ActionSpec {
  action: WorkflowAction;
  label: string;
  target: WorkflowStatus;
  minimumRole: WorkspaceRole;
  confirm?: string;
  destructive?: boolean;
}

const SPECS: Record<WorkflowAction, ActionSpec> = {
  plan: { action: "plan", label: "Start planning", target: "PLANNING", minimumRole: "MEMBER" },
  submit_for_approval: {
    action: "submit_for_approval",
    label: "Submit plan for approval",
    target: "AWAITING_APPROVAL",
    minimumRole: "MEMBER",
  },
  mark_ready: { action: "mark_ready", label: "Mark plan ready", target: "READY", minimumRole: "MEMBER" },
  approve: { action: "approve", label: "Approve plan", target: "READY", minimumRole: "ADMIN" },
  request_changes: {
    action: "request_changes",
    label: "Request changes",
    target: "PLANNING",
    minimumRole: "MEMBER",
  },
  start: { action: "start", label: "Start workflow", target: "RUNNING", minimumRole: "MEMBER" },
  reopen: {
    action: "reopen",
    label: "Reopen planning",
    target: "PLANNING",
    minimumRole: "MEMBER",
    confirm: "The plan goes back to planning and, if it needs approval, must be approved again.",
  },
  pause: { action: "pause", label: "Pause", target: "PAUSED", minimumRole: "MEMBER" },
  resume: { action: "resume", label: "Resume", target: "RUNNING", minimumRole: "MEMBER" },
  unblock: { action: "unblock", label: "Resume (unblock)", target: "RUNNING", minimumRole: "ADMIN" },
  pause_blocked: { action: "pause_blocked", label: "Pause", target: "PAUSED", minimumRole: "MEMBER" },
  cancel: {
    action: "cancel",
    label: "Cancel workflow",
    target: "CANCELLED",
    minimumRole: "MEMBER",
    confirm: "Every unfinished task is cancelled. This cannot be undone.",
    destructive: true,
  },
};

const BY_STATUS: Record<WorkflowStatus, WorkflowAction[]> = {
  PENDING: ["plan", "cancel"],
  PLANNING: ["submit_for_approval", "mark_ready", "cancel"],
  AWAITING_APPROVAL: ["approve", "request_changes", "cancel"],
  READY: ["start", "reopen", "cancel"],
  RUNNING: ["pause", "cancel"],
  PAUSED: ["resume", "cancel"],
  BLOCKED: ["unblock", "pause_blocked", "cancel"],
  COMPLETED: [],
  FAILED: [],
  CANCELLED: [],
};

/** User actions available for a workflow, given its policy and the caller's role. */
export function workflowActions(
  status: WorkflowStatus,
  policy: AutonomyPolicy,
  role: WorkspaceRole,
): ActionSpec[] {
  return BY_STATUS[status]
    .filter((action) => {
      // PLANNING -> READY is only for autonomous workflows; others need approval first.
      if (action === "mark_ready") return policy === "autonomous";
      if (action === "submit_for_approval") return policy === "approval_required";
      return true;
    })
    .map((action) => SPECS[action])
    .filter((spec) => roleAtLeast(role, spec.minimumRole));
}

export const WORKFLOW_STATUS_HELP: Record<WorkflowStatus, string> = {
  PENDING: "Created. Start planning to add tasks.",
  PLANNING: "Add tasks and dependencies, then submit the plan.",
  AWAITING_APPROVAL: "Waiting for an admin to approve the plan.",
  READY: "Approved and ready to start.",
  RUNNING: "Tasks are being assigned and executed.",
  PAUSED: "Nothing new is dispatched until the workflow resumes.",
  BLOCKED: "A required task needs attention before the workflow can continue.",
  COMPLETED: "Every required task completed.",
  FAILED: "The workflow failed.",
  CANCELLED: "The workflow was cancelled.",
};
