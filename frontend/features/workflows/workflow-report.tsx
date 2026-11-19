"use client";

import { useQuery } from "@tanstack/react-query";
import { PrinterIcon } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { ErrorState, PageSkeleton } from "@/components/common/states";
import { StatusBadge } from "@/components/common/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { BreakdownRow, ReportTask, TaskStatus, WorkflowReport, WorkflowStatus } from "@/lib/api/types";
import { formatCost, formatDateTime, humanize } from "@/lib/format";
import { workspacePath } from "@/lib/navigation";
import { useWorkspace } from "@/lib/workspace";

import { workflowKeys } from "./use-workflow";

function duration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${Math.round(seconds % 60)} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card role="region" aria-label={title} className="break-inside-avoid">
      <CardHeader>
        <CardTitle>
          <h2>{title}</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">{children}</CardContent>
    </Card>
  );
}

function Facts({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-[max-content_1fr]">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="font-medium break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * The workflow's execution report (ADR-0042), composed from stored records only. Agent and model
 * text (deliverables, questions, proposed actions) is shown as plain text. Prints cleanly.
 */
export function WorkflowReportView() {
  const workspace = useWorkspace();
  const { workflowId } = useParams<{ workflowId: string }>();
  const report = useQuery({
    queryKey: [...workflowKeys.detail(workspace.id, workflowId), "report"],
    queryFn: () => api.workflows.report(workspace.id, workflowId),
  });
  if (report.isPending) return <PageSkeleton />;
  if (report.isError) return <ErrorState error={report.error} onRetry={() => void report.refetch()} />;
  return <ReportBody report={report.data} workspaceId={workspace.id} />;
}

export function ReportBody({ report, workspaceId }: { report: WorkflowReport; workspaceId: string }) {
  const wf = report.workflow;
  const titles = new Map(report.tasks.map((t) => [t.id, t.title]));
  return (
    <article className="space-y-6" aria-label="Execution report">
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={wf.status as WorkflowStatus} />
          <Badge variant={report.final ? "secondary" : "outline"}>{report.final ? "Final" : "In progress"}</Badge>
          <span className="text-xs text-muted-foreground">
            Generated {formatDateTime(report.generated_at)} · {report.report_version}
          </span>
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">Execution report</h1>
        {!report.final ? (
          <p className="text-sm text-muted-foreground">
            The workflow has not finished; this report shows what has happened so far.
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2 print:hidden">
          <Button variant="outline" onClick={() => window.print()}>
            <PrinterIcon data-icon="inline-start" /> Print or save as PDF
          </Button>
          <Button variant="link" asChild>
            <Link href={workspacePath(workspaceId, `workflows/${wf.id}`)}>Back to the workflow</Link>
          </Button>
        </div>
      </header>

      <Section title="Request">
        <p className="whitespace-pre-wrap break-words">{wf.prompt}</p>
        <Facts
          rows={[
            ["Plan approval", wf.autonomy_policy === "autonomous" ? "Autonomous" : "Approval required"],
            ["Plan version", wf.confirmed_plan_version ?? "None confirmed"],
            ["Budget", wf.budget_limit ? formatCost(wf.budget_limit, wf.currency) : "No budget"],
            ["Created", formatDateTime(wf.created_at)],
            ["Started", wf.started_at ? formatDateTime(wf.started_at) : "Not started"],
            ["Finished", wf.completed_at ? formatDateTime(wf.completed_at) : "Not finished"],
            ["Elapsed", duration(report.metrics.elapsed_seconds)],
          ]}
        />
      </Section>

      <Section title="Tasks and agents">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Task</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Agent</TableHead>
              <TableHead>Depends on</TableHead>
              <TableHead className="text-right">Attempts</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {report.tasks.map((t) => (
              <TableRow key={t.id}>
                <TableCell className="max-w-64 break-words">
                  {t.title}
                  {t.removed ? <span className="text-muted-foreground"> (removed)</span> : null}
                  {!t.required ? <span className="text-muted-foreground"> (optional)</span> : null}
                </TableCell>
                <TableCell>
                  <StatusBadge status={t.status as TaskStatus} />
                </TableCell>
                <TableCell>
                  {t.assigned_agent_name ? `${t.assigned_agent_name} v${t.assigned_agent_version ?? "?"}` : "Unassigned"}
                </TableCell>
                <TableCell>{t.depends_on.map((id) => titles.get(id) ?? "a removed task").join(", ") || "—"}</TableCell>
                <TableCell className="text-right">{t.attempts.length}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Section>

      <Section title="Execution and verification">
        {report.tasks.every((t) => t.attempts.length === 0) ? (
          <p className="text-muted-foreground">No attempts yet.</p>
        ) : (
          report.tasks.filter((t) => t.attempts.length > 0).map((t) => <TaskAttempts key={t.id} task={t} />)
        )}
      </Section>

      <Section title="Deliverables">
        {report.tasks.every((t) => t.deliverables.length === 0) ? (
          <p className="text-muted-foreground">No completed task has deliverables yet.</p>
        ) : (
          report.tasks
            .filter((t) => t.deliverables.length > 0)
            .map((t) => (
              <div key={t.id} className="space-y-2">
                <h3 className="font-medium">{t.title}</h3>
                {t.deliverables.map((d) => (
                  <div key={d.name} className="space-y-1">
                    <p className="text-muted-foreground">{d.name}</p>
                    {d.content === null ? (
                      <p className="text-muted-foreground">No content.</p>
                    ) : (
                      <pre className="max-h-96 overflow-auto rounded-md bg-muted p-3 text-xs whitespace-pre-wrap break-words print:max-h-none">
                        {d.content}
                      </pre>
                    )}
                    {d.truncated ? <p className="text-xs text-muted-foreground">Shortened for the report.</p> : null}
                  </div>
                ))}
              </div>
            ))
        )}
      </Section>

      <Section title="Failures and recovery">
        <Facts
          rows={[
            ["Retries", report.recovery.retries],
            ["Failed attempts", report.recovery.failed_attempts],
            ["Reassignments", report.recovery.reassignments],
            ["Escalations", report.recovery.escalations],
            ["Expired requests", report.recovery.expired_requests],
            ["Failed background jobs", report.recovery.failed_background_jobs],
            [
              "Pauses",
              report.recovery.pauses.length === 0
                ? "None"
                : report.recovery.pauses
                    .map((p) => `${formatDateTime(p.at)} (${humanize(p.pause_reason ?? "unknown")})`)
                    .join("; "),
            ],
          ]}
        />
      </Section>

      <Section title="Human interventions">
        {report.interventions.length === 0 ? (
          <p className="text-muted-foreground">None.</p>
        ) : (
          <ul className="space-y-3">
            {report.interventions.map((i) => (
              <li key={i.id} className="space-y-1">
                <p className="font-medium">
                  {humanize(i.subject)} · {humanize(i.status)}
                  {i.decision ? ` · ${i.decision === "approve" ? "approved" : "rejected"}` : ""}
                  {i.then === "stop" ? ", task stopped" : ""}
                  {i.decided_role ? ` by ${humanize(i.decided_role)}` : ""}
                </p>
                <p className="whitespace-pre-wrap break-words text-muted-foreground">{i.requested_action}</p>
                <p className="text-xs text-muted-foreground">
                  {formatDateTime(i.created_at)}
                  {i.resolved_at ? ` → ${formatDateTime(i.resolved_at)}` : ""}
                  {i.outcome ? ` · ${humanize(i.outcome)}` : ""}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Tokens and costs">
        <Facts
          rows={[
            ["Model calls", report.costs.calls],
            [
              "Tokens",
              `${report.costs.input_tokens.toLocaleString()} in · ${report.costs.output_tokens.toLocaleString()} out`,
            ],
            ...report.costs.totals.flatMap((c): [string, string][] => [
              [`Actual (${c.currency})`, formatCost(c.actual, c.currency)],
              [`Estimated (${c.currency})`, formatCost(c.estimated, c.currency)],
              [`Mock provider (${c.currency}), not a real charge`, formatCost(c.mock, c.currency)],
            ]),
            ["Unpriced calls", report.costs.unpriced_calls],
            [
              "Reconciliation",
              report.costs.reconciled
                ? `All ${report.costs.checked_calls} priced calls match their price snapshot.`
                : `${report.costs.mismatched_calls} of ${report.costs.checked_calls} priced calls do not match their price snapshot.`,
            ],
          ]}
        />
        <UsageTable title="By agent" rows={report.usage.by_agent} />
        <UsageTable title="By model" rows={report.usage.by_model} />
      </Section>
    </article>
  );
}

function TaskAttempts({ task }: { task: ReportTask }) {
  return (
    <div className="space-y-2">
      <h3 className="font-medium">{task.title}</h3>
      <ol className="space-y-2">
        {task.attempts.map((a) => (
          <li key={a.attempt_number} className="rounded-md border p-3">
            <p>
              Attempt {a.attempt_number} · {a.agent_name} v{a.agent_version} · {humanize(a.status)}
              {a.error_code ? ` · ${humanize(a.error_code)}` : ""} · {duration(a.duration_seconds)}
            </p>
            {a.verification.length > 0 ? (
              <ul className="mt-1 text-muted-foreground">
                {a.verification.map((v) => (
                  <li key={v.verifier}>
                    {v.verifier}: {humanize(v.outcome)}
                    {v.issue_codes.length > 0 ? ` (${v.issue_codes.map(humanize).join(", ")})` : ""}
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ol>
    </div>
  );
}

function UsageTable({ title, rows }: { title: string; rows: BreakdownRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="space-y-1">
      <h3 className="font-medium">{title}</h3>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead className="text-right">Calls</TableHead>
            <TableHead className="text-right">Actual</TableHead>
            <TableHead className="text-right">Estimated</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.key}>
              <TableCell>{r.label}</TableCell>
              <TableCell className="text-right">{r.calls}</TableCell>
              <TableCell className="text-right">
                {r.costs.map((c) => formatCost(c.actual, c.currency)).join(" · ") || "—"}
              </TableCell>
              <TableCell className="text-right">
                {r.costs.map((c) => formatCost(c.estimated, c.currency)).join(" · ") || "—"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
