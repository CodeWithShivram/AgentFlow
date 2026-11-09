import { Badge } from "@/components/ui/badge";
import type {
  AgentStatus,
  ExecutionStatus,
  ProposalStatus,
  TaskStatus,
  WorkflowStatus,
} from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { cn } from "@/lib/utils";

type Tone = "neutral" | "info" | "progress" | "success" | "warning" | "danger";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-muted text-muted-foreground",
  info: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200",
  progress: "bg-indigo-100 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-200",
  success: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
  warning: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  danger: "bg-red-100 text-red-900 dark:bg-red-950 dark:text-red-200",
};

type AnyStatus = WorkflowStatus | TaskStatus | ExecutionStatus | AgentStatus | ProposalStatus;

const TONES: Partial<Record<AnyStatus, Tone>> = {
  PLANNING: "info",
  AWAITING_APPROVAL: "warning",
  READY: "info",
  RUNNING: "progress",
  PAUSED: "warning",
  BLOCKED: "danger",
  COMPLETED: "success",
  FAILED: "danger",
  ASSIGNED: "info",
  QUEUED: "progress",
  IN_PROGRESS: "progress",
  SUBMITTED: "progress",
  VERIFYING: "progress",
  REWORK_REQUIRED: "warning",
  WAITING_APPROVAL: "warning",
  ESCALATED: "danger",
  SUCCEEDED: "success",
  TIMED_OUT: "danger",
  ACTIVE: "success",
  APPROVED: "success",
  REJECTED: "danger",
};

export function statusTone(status: AnyStatus): Tone {
  return TONES[status] ?? "neutral";
}

/** A status label. The text carries the meaning; colour is only a secondary cue. */
export function StatusBadge({ status, className }: { status: AnyStatus; className?: string }) {
  return (
    <Badge
      variant="secondary"
      className={cn("border-transparent font-medium", TONE_CLASSES[statusTone(status)], className)}
    >
      {humanize(status)}
    </Badge>
  );
}
