"use client";

import { useQuery } from "@tanstack/react-query";

import { RelativeTime } from "@/components/common/relative-time";
import { StatusBadge } from "@/components/common/status-badge";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { describeError } from "@/lib/api/errors";
import type {
  AcceptanceCriterion,
  Agent,
  AgentSubmission,
  CriterionStatus,
  EvaluationFindings,
  EvaluationVerdict,
  Task,
  Verification,
  VerificationIssue,
} from "@/lib/api/types";
import { useWorkspace } from "@/lib/workspace";

import { TASK_IN_FLIGHT } from "./status";
import { workflowKeys } from "./use-workflow";

interface ExecutionsPanelProps {
  task: Task;
  agentsById: Map<string, Agent>;
}

const CLAIM_LABELS: Record<CriterionStatus, string> = {
  MET: "Met",
  PARTIAL: "Partly met",
  NOT_MET: "Not met",
};

/** Every attempt of a task with the exact agent version it ran. Refreshed by the event poll. */
export function ExecutionsPanel({ task, agentsById }: ExecutionsPanelProps) {
  const workspace = useWorkspace();
  const executions = useQuery({
    queryKey: workflowKeys.executions(workspace.id, task.workflow_id, task.id),
    queryFn: () => api.tasks.executions(workspace.id, task.workflow_id, task.id),
    // Belt and braces next to the event poll: in-flight attempts also refresh on their own.
    refetchInterval: TASK_IN_FLIGHT.has(task.status) ? 5_000 : false,
  });

  if (executions.isPending) return <p className="text-sm text-muted-foreground">Loading attempts…</p>;
  if (executions.isError) return <p className="text-sm text-destructive">{describeError(executions.error)}</p>;
  if (executions.data.items.length === 0) {
    return <p className="text-sm text-muted-foreground">No execution attempts yet.</p>;
  }
  return (
    <ol className="space-y-2" aria-label={`Execution attempts for ${task.title}`}>
      {executions.data.items.map((e) => (
        <li key={e.id} className="rounded-md border p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">Attempt {e.attempt_number}</span>
            <StatusBadge status={e.status} />
            <span className="text-muted-foreground">
              {agentsById.get(e.agent_id)?.name ?? "Agent"} · version {e.agent_version}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Started <RelativeTime value={e.started_at} />
            {e.completed_at ? (
              <>
                {" "}
                · finished <RelativeTime value={e.completed_at} />
              </>
            ) : null}
          </p>
          {e.result_summary ? <p className="mt-2 whitespace-pre-wrap break-words">{e.result_summary}</p> : null}
          {e.submission ? <SubmissionDetails submission={e.submission} criteria={task.acceptance_criteria} /> : null}
          {e.verifications.map((v) => (
            <VerificationDetails key={v.id} verification={v} criteria={task.acceptance_criteria} />
          ))}
          {e.error_summary ? (
            <p className="mt-2 text-destructive">
              {e.error_code ? <span className="font-mono text-xs">{e.error_code}: </span> : null}
              {e.error_summary}
            </p>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

/**
 * The structured submission of a successful attempt (Step 3.1). Agent output is untrusted and
 * rendered as plain text; criterion claims are the agent's own until verification checks them.
 */
function SubmissionDetails({ submission, criteria }: { submission: AgentSubmission; criteria: AcceptanceCriterion[] }) {
  const descriptions = new Map(criteria.map((c) => [c.id, c.description]));
  return (
    <div className="mt-2 space-y-2">
      {submission.criteria.length > 0 ? (
        <section aria-label="Agent's criterion claims">
          <p className="text-xs font-medium text-muted-foreground">Agent&apos;s claims (not verified yet)</p>
          <ul className="mt-1 space-y-1">
            {submission.criteria.map((claim) => (
              <li key={claim.criterion_id} className="break-words">
                <Badge variant="outline" className="mr-2">
                  {CLAIM_LABELS[claim.status]}
                </Badge>
                {descriptions.get(claim.criterion_id) ?? claim.criterion_id}
                <span className="block text-xs text-muted-foreground">{claim.evidence}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <ProposedFiles title="Proposed files (not applied)" files={proposedFiles(submission.outputs.proposed_files)} />
      <ProposedFiles title="Proposed tests (not run)" files={proposedFiles(submission.outputs.proposed_tests)} />
      <TestCases cases={testCases(submission.outputs.test_cases)} descriptions={descriptions} />
      <Notes title="Open questions" items={submission.open_questions} />
      <Notes title="Assumptions" items={submission.assumptions} />
      <Notes title="Limitations" items={submission.limitations} />
    </div>
  );
}

const ISSUE_LABELS: Record<string, string> = {
  SUBMISSION_MISSING: "No submission was stored",
  SUBMISSION_INVALID: "The submission could not be read",
  DUPLICATE_CRITERION_CLAIM: "More than one claim for a criterion",
  MISSING_CRITERION_CLAIM: "No claim for a criterion",
  UNKNOWN_CRITERION: "Claims for criteria the task does not have",
  CRITERION_NOT_MET: "The agent says this criterion is not met",
  CRITERION_PARTIAL: "The agent says this criterion is only partly met",
  DELIVERABLE_MISSING: "Deliverable missing or empty",
  OUTPUT_SCHEMA_INVALID: "The task's output schema is not valid JSON Schema",
  OUTPUT_SCHEMA_UNUSABLE: "The task's output schema could not be applied",
  OUTPUT_SCHEMA_MISMATCH: "Outputs do not match the task's output schema",
  OUTPUT_SCHEMA_MORE_MISMATCHES: "Further schema mismatches are not listed",
  OUTPUTS_INVALID: "Outputs do not fit the structure this agent must produce",
  TEST_CASE_UNKNOWN_CRITERION: "Test cases cover criteria the task does not have",
  PLATFORM_NOTE_MISSING: "The platform's limitation note is missing",
  // Semantic evaluation (Step 5.2)
  OBJECTIVE_NOT_MET: "The evaluator judged the objective not met",
  OBJECTIVE_PARTIAL: "The evaluator judged the objective only partly met",
  CRITERION_JUDGED_NOT_MET: "The evaluator judged this criterion not met",
  CRITERION_JUDGED_PARTIAL: "The evaluator judged this criterion only partly met",
  CONFLICTS_WITH_REQUIREMENTS: "The result conflicts with the requirements",
  INCOMPLETE: "The evaluator found the work incomplete",
  INCONSISTENT: "The evaluator found contradictions in the work",
  ASSUMPTIONS_NOT_EXPLICIT: "The work relies on unstated assumptions",
};

const VERIFIER_LABELS: Record<string, string> = {
  deterministic: "deterministic checks",
  semantic: "semantic evaluation",
};

const VERDICT_LABELS: Record<EvaluationVerdict, string> = {
  met: "Met",
  partially_met: "Partly met",
  not_met: "Not met",
};

/** "semantic/v1" -> "semantic evaluation"; unknown verifiers keep their name. */
export function verifierLabel(verifier: string): string {
  const kind = verifier.split("/")[0];
  return VERIFIER_LABELS[kind] ?? kind;
}

/** The issue's subject, from task data only: a criterion, a deliverable or a schema location. */
function issueSubject(issue: VerificationIssue, descriptions: Map<string, string>): string | null {
  if (issue.criterion_id) return descriptions.get(issue.criterion_id) ?? issue.criterion_id;
  if (issue.deliverable) return issue.deliverable;
  if (issue.location) return `at ${issue.location}`;
  return null;
}

/** Platform verification of one attempt (Step 5.1): deterministic checks, not a content review. */
function VerificationDetails({ verification, criteria }: { verification: Verification; criteria: AcceptanceCriterion[] }) {
  const descriptions = new Map(criteria.map((c) => [c.id, c.description]));
  const passed = verification.outcome === "PASSED";
  const label = verifierLabel(verification.verifier);
  return (
    <section aria-label={`Platform verification: ${label}`} className="mt-2 rounded border px-2 py-1">
      <p className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium">Platform verification: {label}</span>
        <Badge variant={passed ? "secondary" : "destructive"}>{passed ? "Passed" : "Failed"}</Badge>
        <span className="font-mono text-muted-foreground">{verification.verifier}</span>
      </p>
      {verification.issues.length > 0 ? (
        <ul className="mt-1 space-y-1">
          {verification.issues.map((issue, i) => {
            const subject = issueSubject(issue, descriptions);
            return (
              <li key={i} className="break-words text-xs">
                <span className={issue.severity === "error" ? "text-destructive" : "text-muted-foreground"}>
                  {issue.severity === "error" ? "Error" : "Warning"}:
                </span>{" "}
                {ISSUE_LABELS[issue.code] ?? issue.code}
                {subject ? <span className="text-muted-foreground"> · {subject}</span> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      {verification.findings ? <EvaluatorFindings findings={verification.findings} descriptions={descriptions} /> : null}
    </section>
  );
}

/** The semantic evaluator's findings (Step 5.2): model output, rendered as plain text only. */
function EvaluatorFindings({ findings, descriptions }: { findings: EvaluationFindings; descriptions: Map<string, string> }) {
  return (
    <div className="mt-1 space-y-1 text-xs" aria-label="Evaluator findings" role="group">
      <p className="break-words">
        <Badge variant="outline" className="mr-2">
          Objective: {VERDICT_LABELS[findings.objective]}
        </Badge>
        {findings.objective_reason}
      </p>
      {findings.criteria.length > 0 ? (
        <ul className="space-y-1">
          {findings.criteria.map((finding) => (
            <li key={finding.criterion_id} className="break-words">
              <Badge variant="outline" className="mr-2">
                {VERDICT_LABELS[finding.verdict]}
              </Badge>
              {descriptions.get(finding.criterion_id) ?? finding.criterion_id}
              <span className="block text-muted-foreground">Evidence: {finding.evidence}</span>
              <span className="block text-muted-foreground">Reason: {finding.reason}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <p className="break-words text-muted-foreground">{findings.summary}</p>
    </div>
  );
}

// Developer and QA outputs (Step 3.5). Outputs are untrusted agent data: they are read
// defensively and rendered as plain text only.
interface ProposedFile {
  path: string;
  language: string;
  purpose: string;
  content: string;
}

interface TestCase {
  id: string;
  title: string;
  covers: string[];
  kind: string;
  steps: string[];
  expected: string;
}

const isText = (value: unknown): value is string => typeof value === "string";
const isTextList = (value: unknown): value is string[] => Array.isArray(value) && value.every(isText);

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
    : [];
}

function proposedFiles(value: unknown): ProposedFile[] {
  return records(value).flatMap((f) =>
    isText(f.path) && isText(f.language) && isText(f.purpose) && isText(f.content)
      ? [{ path: f.path, language: f.language, purpose: f.purpose, content: f.content }]
      : [],
  );
}

function testCases(value: unknown): TestCase[] {
  return records(value).flatMap((c) =>
    isText(c.id) && isText(c.title) && isTextList(c.covers) && isText(c.kind) && isTextList(c.steps) && isText(c.expected)
      ? [{ id: c.id, title: c.title, covers: c.covers, kind: c.kind, steps: c.steps, expected: c.expected }]
      : [],
  );
}

function ProposedFiles({ title, files }: { title: string; files: ProposedFile[] }) {
  if (files.length === 0) return null;
  return (
    <section aria-label={title}>
      <p className="text-xs font-medium text-muted-foreground">{title}</p>
      <ul className="mt-1 space-y-1">
        {files.map((file) => (
          <li key={file.path}>
            <details className="rounded border px-2 py-1">
              <summary className="cursor-pointer break-all font-mono text-xs">
                {file.path} <span className="font-sans text-muted-foreground">· {file.language}</span>
              </summary>
              <p className="mt-1 break-words text-xs">{file.purpose}</p>
              <pre className="mt-1 max-h-72 overflow-auto rounded bg-muted p-2 text-xs whitespace-pre">{file.content}</pre>
            </details>
          </li>
        ))}
      </ul>
    </section>
  );
}

function TestCases({ cases, descriptions }: { cases: TestCase[]; descriptions: Map<string, string> }) {
  if (cases.length === 0) return null;
  return (
    <section aria-label="Test cases (designed, not run)">
      <p className="text-xs font-medium text-muted-foreground">Test cases (designed, not run)</p>
      <ul className="mt-1 space-y-2">
        {cases.map((c) => (
          <li key={c.id} className="break-words">
            <span className="font-medium">{c.title}</span>{" "}
            <span className="text-xs text-muted-foreground">
              · {c.kind}
              {c.covers.length > 0 ? ` · covers ${c.covers.map((id) => descriptions.get(id) ?? id).join("; ")}` : ""}
            </span>
            <ol className="mt-1 list-decimal pl-5 text-xs">
              {c.steps.map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ol>
            <p className="text-xs">Expected: {c.expected}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Notes({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <section aria-label={title}>
      <p className="text-xs font-medium text-muted-foreground">{title}</p>
      <ul className="mt-1 list-disc space-y-1 pl-5">
        {items.map((item, i) => (
          <li key={i} className="break-words">
            {item}
          </li>
        ))}
      </ul>
    </section>
  );
}
