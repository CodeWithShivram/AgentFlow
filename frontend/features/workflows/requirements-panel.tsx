"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/common/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { describeError } from "@/lib/api/errors";
import type { PromptAnalysis, Workflow } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { useCan, useWorkspace } from "@/lib/workspace";

import { workflowKeys } from "./use-workflow";

const PRIORITY_LABEL = { must: "Must", should: "Should", could: "Could" } as const;

/**
 * The prompt analysis of a workflow (Step 2.2). It is produced in the background after planning
 * starts; the page polls while it is pending. Model output is shown as plain text only.
 */
export function RequirementsPanel({ workflow }: { workflow: Workflow }) {
  const status = workflow.analysis_status;
  if (status === "PENDING") {
    return (
      <div role="status" className="flex items-center gap-3 rounded-lg border p-4 text-sm">
        <LoaderCircleIcon className="size-4 animate-spin" aria-hidden />
        Analysing the request… Requirements appear here when the analysis is ready.
      </div>
    );
  }
  if (status === "FAILED") return <AnalysisFailed workflow={workflow} />;
  if (status === "NOT_REQUESTED" || !workflow.normalized_requirements) {
    return (
      <EmptyState
        title="Not analysed yet"
        description={
          workflow.status === "PENDING"
            ? "Start planning to analyse the request into requirements and constraints."
            : "This workflow has no analysis."
        }
      />
    );
  }
  return <AnalysisView analysis={workflow.normalized_requirements} />;
}

function AnalysisFailed({ workflow }: { workflow: Workflow }) {
  const workspace = useWorkspace();
  const canRetry = useCan("MEMBER");
  const queryClient = useQueryClient();
  const retry = useMutation({
    mutationFn: () => api.workflows.retryAnalysis(workspace.id, workflow.id),
    onSuccess: (updated) => {
      queryClient.setQueryData(workflowKeys.detail(workspace.id, workflow.id), updated);
      void queryClient.invalidateQueries({ queryKey: workflowKeys.events(workspace.id, workflow.id) });
      toast.success("Analysis requested again");
    },
    onError: (error) => toast.error(describeError(error)),
  });
  return (
    <Alert variant="destructive">
      <AlertTitle>The analysis failed</AlertTitle>
      <AlertDescription className="space-y-3">
        <p>
          The model did not return a valid analysis after several attempts. You can still plan the
          tasks yourself{canRetry ? ", or request the analysis again" : ""}.
        </p>
        {canRetry ? (
          <Button
            variant="outline"
            size="sm"
            disabled={retry.isPending}
            aria-busy={retry.isPending}
            onClick={() => retry.mutate()}
          >
            <RefreshCwIcon data-icon="inline-start" />
            {retry.isPending ? "Requesting…" : "Retry analysis"}
          </Button>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

function AnalysisView({ analysis }: { analysis: PromptAnalysis }) {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>
            <h2>Objective</h2>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm break-words">{analysis.objective}</p>
          {analysis.suggested_skills.length > 0 ? (
            <ul className="flex flex-wrap gap-1" aria-label="Suggested skills">
              {analysis.suggested_skills.map((skill) => (
                <li key={skill}>
                  <Badge variant="secondary">{humanize(skill)}</Badge>
                </li>
              ))}
            </ul>
          ) : null}
        </CardContent>
      </Card>

      {analysis.ambiguities.length > 0 ? (
        <Alert>
          <AlertTitle>Open questions ({analysis.ambiguities.length})</AlertTitle>
          <AlertDescription>
            <ul className="mt-2 space-y-2">
              {analysis.ambiguities.map((a) => (
                <li key={a.id}>
                  <p className="font-medium text-foreground">{a.question}</p>
                  <p>{a.impact}</p>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs">Answering these as clarification requests arrives in a later phase.</p>
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Requirements">
          <ul className="space-y-2">
            {analysis.requirements.map((r) => (
              <li key={r.id} className="flex items-start gap-2 text-sm">
                <Badge variant={r.priority === "must" ? "default" : "outline"} className="shrink-0">
                  {PRIORITY_LABEL[r.priority]}
                </Badge>
                <span className="break-words">{r.description}</span>
              </li>
            ))}
          </ul>
        </Section>
        <Section title="Constraints" empty="No constraints identified.">
          {analysis.constraints.length > 0 ? (
            <ul className="space-y-2">
              {analysis.constraints.map((c) => (
                <li key={c.id} className="flex items-start gap-2 text-sm">
                  <Badge variant="outline" className="shrink-0">
                    {humanize(c.kind)}
                  </Badge>
                  <span className="break-words">{c.description}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </Section>
        <Section title="Deliverables" empty="No deliverables identified.">
          {analysis.deliverables.length > 0 ? <TextList items={analysis.deliverables} /> : null}
        </Section>
        <Section title="Assumptions" empty="No assumptions recorded.">
          {analysis.assumptions.length > 0 ? <TextList items={analysis.assumptions} /> : null}
        </Section>
      </div>
    </div>
  );
}

function Section({ title, empty, children }: { title: string; empty?: string; children: React.ReactNode }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>
          <h3>{title}</h3>
        </CardTitle>
      </CardHeader>
      <CardContent>{children ?? <p className="text-sm text-muted-foreground">{empty}</p>}</CardContent>
    </Card>
  );
}

function TextList({ items }: { items: string[] }) {
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm">
      {items.map((item, index) => (
        <li key={index} className="break-words">
          {item}
        </li>
      ))}
    </ul>
  );
}
