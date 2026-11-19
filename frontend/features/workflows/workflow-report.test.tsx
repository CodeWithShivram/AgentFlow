import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { WorkflowReport } from "@/lib/api/types";

import { ReportBody } from "./workflow-report";

const TOTALS = { calls: 1, unpriced_calls: 0, input_tokens: 100, output_tokens: 50, cached_input_tokens: 0, costs: [] };

const REPORT: WorkflowReport = {
  report_version: "report/v1",
  generated_at: "2026-11-19T12:00:00Z",
  final: true,
  workflow: {
    id: "wf-1",
    prompt: "Write the launch report",
    status: "COMPLETED",
    pause_reason: null,
    autonomy_policy: "autonomous",
    budget_limit: null,
    currency: "USD",
    max_parallel_tasks: 3,
    deadline: null,
    confirmed_plan_version: 1,
    created_at: "2026-11-19T10:00:00Z",
    started_at: "2026-11-19T10:01:00Z",
    completed_at: "2026-11-19T10:31:00Z",
  },
  tasks: [
    {
      id: "t-1",
      title: "Draft the report",
      status: "COMPLETED",
      required: true,
      removed: false,
      priority: 0,
      required_skills: ["report_writing"],
      depends_on: [],
      assigned_agent_id: "ag-1",
      assigned_agent_name: "Writer",
      assigned_agent_version: 1,
      started_at: "2026-11-19T10:01:00Z",
      completed_at: "2026-11-19T10:30:00Z",
      attempts: [
        {
          attempt_number: 1,
          agent_id: "ag-1",
          agent_name: "Writer",
          agent_version: 1,
          status: "FAILED",
          error_code: "EXECUTION_FAILED",
          started_at: "2026-11-19T10:01:00Z",
          completed_at: "2026-11-19T10:01:30Z",
          duration_seconds: 30,
          verification: [],
        },
        {
          attempt_number: 2,
          agent_id: "ag-1",
          agent_name: "Writer",
          agent_version: 1,
          status: "SUCCEEDED",
          error_code: null,
          started_at: "2026-11-19T10:02:00Z",
          completed_at: "2026-11-19T10:03:00Z",
          duration_seconds: 60,
          verification: [
            { verifier: "deterministic/v1", outcome: "PASSED", issue_codes: [] },
            { verifier: "semantic/v1", outcome: "PASSED", issue_codes: ["CRITERION_PARTIAL"] },
          ],
        },
      ],
      deliverables: [{ name: "Report", content: "<script>alert(1)</script> Findings", truncated: true }],
    },
  ],
  interventions: [
    {
      id: "i-1",
      task_id: "t-1",
      type: "APPROVAL",
      subject: "agent_action",
      status: "RESOLVED",
      requested_action: "Email the customer list",
      requested_by: "AGENT",
      created_at: "2026-11-19T10:05:00Z",
      resolved_at: "2026-11-19T10:06:00Z",
      decision: "reject",
      then: "stop",
      outcome: null,
      decided_role: "ADMIN",
    },
  ],
  recovery: {
    retries: 1,
    failed_attempts: 1,
    reassignments: 0,
    escalations: 0,
    expired_requests: 0,
    failed_background_jobs: 0,
    pauses: [],
  },
  metrics: {
    tasks: 1,
    tasks_by_status: { COMPLETED: 1 },
    attempts: 2,
    attempts_by_status: { FAILED: 1, SUCCEEDED: 1 },
    verifications_passed: 2,
    verifications_failed: 0,
    elapsed_seconds: 1800,
  },
  costs: {
    calls: 1,
    calls_by_status: { SUCCEEDED: 1 },
    unpriced_calls: 0,
    input_tokens: 100,
    output_tokens: 50,
    cached_input_tokens: 0,
    totals: [{ currency: "USD", actual: "0.00120000", estimated: "0", mock: "0" }],
    budget: null,
    checked_calls: 1,
    mismatched_calls: 0,
    reconciled: true,
  },
  usage: {
    workflow_id: "wf-1",
    totals: TOTALS,
    by_task: [],
    by_agent: [{ ...TOTALS, key: "platform:semantic_evaluation", label: "Platform: semantic_evaluation" }],
    by_model: [],
  },
  requirements: null,
};

describe("ReportBody", () => {
  it("tells the workflow's story from its records, as plain text", () => {
    render(<ReportBody report={REPORT} workspaceId="ws-1" />);
    const report = screen.getByRole("article", { name: "Execution report" });
    expect(within(report).getByText("Final")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Request" })).toHaveTextContent("Elapsed30 min 0 s");

    const execution = screen.getByRole("region", { name: "Execution and verification" });
    expect(execution).toHaveTextContent("Attempt 1 · Writer v1 · Failed · Execution failed · 30.0 s");
    expect(execution).toHaveTextContent("semantic/v1: Passed (Criterion partial)");

    const deliverables = screen.getByRole("region", { name: "Deliverables" });
    expect(deliverables).toHaveTextContent("<script>alert(1)</script> Findings");
    expect(deliverables.querySelector("script")).toBeNull();
    expect(deliverables).toHaveTextContent("Shortened for the report.");

    expect(screen.getByRole("region", { name: "Failures and recovery" })).toHaveTextContent("Retries1");
    expect(screen.getByRole("region", { name: "Human interventions" })).toHaveTextContent(
      "Agent action · Resolved · rejected, task stopped by Admin",
    );
    const costs = screen.getByRole("region", { name: "Tokens and costs" });
    expect(costs).toHaveTextContent("Actual (USD)$0.0012");
    expect(costs).toHaveTextContent("All 1 priced calls match their price snapshot.");
    expect(costs).toHaveTextContent("Platform: semantic_evaluation");
  });

  it("marks a report of a running workflow as in progress", () => {
    render(
      <ReportBody
        report={{ ...REPORT, final: false, workflow: { ...REPORT.workflow, status: "RUNNING", completed_at: null } }}
        workspaceId="ws-1"
      />,
    );
    expect(screen.getByText("In progress")).toBeInTheDocument();
    expect(screen.getByText("The workflow has not finished; this report shows what has happened so far.")).toBeInTheDocument();
  });
});
