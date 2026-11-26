// Display rules for software projects (ADR-0053). Every number comes from the API; these
// helpers only choose words and round the API's own range for reading.

import type { Tone } from "@/components/common/status-badge";
import type {
  EstimateConfidence,
  MilestoneState,
  ProjectPhase,
  ProjectProgress,
  Workflow,
  WorkflowStatus,
} from "@/lib/api/types";

export const PHASE_LABELS: Record<ProjectPhase, string> = {
  PLANNING: "Planning",
  AWAITING_APPROVAL: "Awaiting approval",
  BUILDING: "Building",
  INTEGRATING: "Integrating",
  PACKAGING: "Packaging",
  COMPLETED: "Completed",
  NEEDS_ATTENTION: "Needs attention",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
};

export const PHASE_TONES: Record<ProjectPhase, Tone> = {
  PLANNING: "info",
  AWAITING_APPROVAL: "warning",
  BUILDING: "progress",
  INTEGRATING: "progress",
  PACKAGING: "progress",
  COMPLETED: "success",
  NEEDS_ATTENTION: "warning",
  FAILED: "danger",
  CANCELLED: "neutral",
};

export const MILESTONE_LABELS: Record<MilestoneState, string> = {
  not_applicable: "Not applicable",
  pending: "Not started",
  running: "Running",
  passed: "Passed",
  failed: "Failed",
  ready: "Ready",
};

const CONFIDENCE_LABELS: Record<Exclude<EstimateConfidence, "none">, string> = {
  low: "low confidence",
  medium: "medium confidence",
  high: "high confidence",
};

const TERMINAL_PHASES: ReadonlySet<ProjectPhase> = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
const MILESTONE_BUSY: ReadonlySet<MilestoneState> = new Set(["pending", "running"]);

/** Statuses in which an admin may package the current state (the API answers 409 otherwise). */
export const PACKAGEABLE_STATUSES: ReadonlySet<WorkflowStatus> = new Set(["BLOCKED", "PAUSED", "COMPLETED"]);

/**
 * Whether the project page keeps polling: until the project is finished, and after completion
 * while a requested package is still being made.
 */
export function progressIsLive(progress: ProjectProgress | undefined): boolean {
  if (!progress) return false;
  if (!TERMINAL_PHASES.has(progress.phase)) return true;
  return progress.phase === "COMPLETED" && MILESTONE_BUSY.has(progress.package);
}

/** A project's display name: its confirmed spec's name, else the start of its prompt. */
export function projectName(workflow: Pick<Workflow, "prompt" | "project_spec">, max = 80): string {
  const name = workflow.project_spec?.name?.trim();
  if (name) return name;
  const prompt = workflow.prompt.trim().replace(/\s+/g, " ");
  return prompt.length > max ? `${prompt.slice(0, max - 1)}…` : prompt;
}

type Unit = { size: number; label: string };
const MINUTE: Unit = { size: 60, label: "min" };
const HOUR: Unit = { size: 3600, label: "h" };
const DAY: Unit = { size: 86_400, label: "days" };

/**
 * The API's time-remaining range in words, e.g. "about 4–9 min". Always a range: the low end
 * rounds down and the high end up, so rounding never narrows what the API estimated.
 */
export function formatRemainingRange(lowSeconds: number, highSeconds: number): string {
  const low = Math.max(0, Math.min(lowSeconds, highSeconds));
  const high = Math.max(lowSeconds, highSeconds, 0);
  if (high < 60) return "under a minute";
  const unit = high < 2 * 3600 ? MINUTE : high < 2 * 86_400 ? HOUR : DAY;
  const lo = Math.floor(low / unit.size);
  let hi = Math.ceil(high / unit.size);
  if (hi <= lo) hi = lo + 1;
  return `about ${lo}–${hi} ${unit.label}`;
}

/** The remaining-time line: a range with its confidence, or the API's reason for having none. */
export function describeRemaining(remaining: ProjectProgress["remaining"]): {
  range: string | null;
  confidence: string | null;
  explanation: string;
} {
  if (
    remaining.confidence === "none" ||
    remaining.low_seconds === null ||
    remaining.high_seconds === null
  ) {
    return { range: null, confidence: null, explanation: remaining.explanation };
  }
  return {
    range: formatRemainingRange(remaining.low_seconds, remaining.high_seconds),
    confidence: CONFIDENCE_LABELS[remaining.confidence],
    explanation: remaining.explanation,
  };
}

/** Why a package failed, for people (backend app/projects/package_job.py, packaging.py). */
export const PACKAGE_ERROR_HELP: Record<string, string> = {
  SECRET_DETECTED: "A likely secret was found in the project; nothing was packaged.",
  NOTHING_TO_PACKAGE: "The project has no files to package yet.",
  TOO_MANY_FILES: "The project has more files than a package may hold.",
  FILE_TOO_LARGE: "A file is larger than a package allows.",
  PACKAGE_TOO_LARGE: "The project is larger than a package allows.",
  PATH_REFUSED: "A file path was not safe to include; nothing was packaged.",
  LINK_REFUSED: "The project contains a link that cannot be packaged safely.",
  ARCHIVE_INVALID: "The archive could not be built correctly.",
  ARCHIVE_CORRUPT: "The archive failed its integrity check after it was built.",
  WORKER_LOST: "The packaging job stopped unexpectedly. Request a new package.",
};

export function packageErrorHelp(code: string | null): string {
  if (!code) return "Packaging failed.";
  return PACKAGE_ERROR_HELP[code] ?? "Packaging failed.";
}
