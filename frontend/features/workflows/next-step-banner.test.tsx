import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { NextStep, Workflow, WorkflowEvent } from "@/lib/api/types";

import { NextStepBanner } from "./next-step-banner";
import { assessmentIsStale } from "./use-workflow";

function step(overrides: Partial<NextStep> = {}): NextStep {
  return {
    kind: "assign_agents",
    actor: "people",
    summary: "Assign agents to 2 tasks (1 task running).",
    task_ids: ["t-1", "t-2"],
    assessed_at: new Date().toISOString(),
    graph_version: "orchestration/v1",
    ...overrides,
  };
}

describe("NextStepBanner", () => {
  it("says what happens next and who acts", () => {
    render(<NextStepBanner nextStep={step()} />);
    const banner = screen.getByRole("region", { name: "Next step" });
    expect(banner).toHaveTextContent("Next step for you");
    expect(banner).toHaveTextContent("Assign agents to 2 tasks (1 task running).");
    expect(banner).toHaveTextContent("Assessed");
  });

  it("names the platform when work waits for verification", () => {
    render(<NextStepBanner nextStep={step({ kind: "awaiting_verification", actor: "platform", summary: "1 task submitted." })} />);
    expect(screen.getByRole("region", { name: "Next step" })).toHaveTextContent("Waiting for the platform");
  });

  it("stays hidden before the first assessment and once the workflow is finished", () => {
    const { rerender } = render(<NextStepBanner nextStep={null} />);
    expect(screen.queryByRole("region", { name: "Next step" })).not.toBeInTheDocument();
    rerender(<NextStepBanner nextStep={step({ kind: "finished", actor: "none" })} />);
    expect(screen.queryByRole("region", { name: "Next step" })).not.toBeInTheDocument();
  });
});

describe("assessmentIsStale", () => {
  const event = (created_at: string) => ({ created_at }) as WorkflowEvent;
  const workflow = (assessed_at: string | null) =>
    ({ next_step: assessed_at ? step({ assessed_at }) : null }) as Workflow;

  it("is stale until the assessment is newer than the latest event", () => {
    const events = [event("2026-11-14T10:00:00Z"), event("2026-11-14T10:00:05Z")];
    expect(assessmentIsStale(workflow(null), events)).toBe(true);
    expect(assessmentIsStale(workflow("2026-11-14T10:00:01Z"), events)).toBe(true);
    expect(assessmentIsStale(workflow("2026-11-14T10:00:06Z"), events)).toBe(false);
    expect(assessmentIsStale(workflow(null), [])).toBe(false);
    expect(assessmentIsStale(undefined, events)).toBe(false);
  });
});
