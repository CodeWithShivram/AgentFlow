"use client";

import { useQuery } from "@tanstack/react-query";

import { RelativeTime } from "@/components/common/relative-time";
import { StatusBadge } from "@/components/common/status-badge";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { describeError } from "@/lib/api/errors";
import type { AcceptanceCriterion, Agent, AgentSubmission, CriterionStatus, Task } from "@/lib/api/types";
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
