"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PlusIcon, RadioIcon } from "lucide-react";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { RelativeTime } from "@/components/common/relative-time";
import { RevisionConflictDialog } from "@/components/common/revision-conflict-dialog";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/common/states";
import { StatusBadge } from "@/components/common/status-badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import type { Agent, Task, Workflow } from "@/lib/api/types";
import { formatDateTime, formatMoney } from "@/lib/format";
import { useActionErrors } from "@/lib/hooks/use-action-errors";
import { useCan, useWorkspace } from "@/lib/workspace";

import { AssignAgentDialog } from "./assign-agent-dialog";
import { DependenciesDialog } from "./dependencies-dialog";
import { DependencyGraph } from "./dependency-graph";
import { EventTimeline } from "./event-timeline";
import { PlanDraftPreview } from "./plan-draft-preview";
import { RequirementsPanel } from "./requirements-panel";
import { PLAN_EDIT_REOPENS, PLAN_EDITABLE, WORKFLOW_STATUS_HELP } from "./status";
import { type TaskAction, TaskList } from "./task-list";
import { TaskFormDialog } from "./task-form-dialog";
import { useWorkflow, workflowKeys } from "./use-workflow";
import { WorkflowActions } from "./workflow-actions";

export function WorkflowDetailView() {
  const workspace = useWorkspace();
  const { workflowId } = useParams<{ workflowId: string }>();
  const queryClient = useQueryClient();
  const canEdit = useCan("MEMBER");
  const { workflow, tasks, events, live } = useWorkflow(workspace.id, workflowId);
  const errors = useActionErrors();

  const agents = useQuery({
    queryKey: ["agents", workspace.id, "list", { limit: 100, offset: 0 }],
    queryFn: () => api.agents.list(workspace.id, { limit: 100 }),
  });
  const agentsById = new Map<string, Agent>((agents.data?.items ?? []).map((a) => [a.id, a]));

  const [taskForm, setTaskForm] = useState<{ open: boolean; task: Task | null }>({ open: false, task: null });
  const [dependencyTask, setDependencyTask] = useState<Task | null>(null);
  const [assignTask, setAssignTask] = useState<Task | null>(null);
  const [confirmTask, setConfirmTask] = useState<{ action: "cancel" | "remove"; task: Task } | null>(null);
  const [tab, setTab] = useState("tasks");

  const refresh = () => queryClient.invalidateQueries({ queryKey: workflowKeys.detail(workspace.id, workflowId) });
  const reloadAfterConflict = () => {
    errors.clearConflict();
    void refresh();
  };

  const taskChange = useMutation({
    mutationFn: async ({ action, task }: { action: "cancel" | "remove"; task: Task }): Promise<void> => {
      if (action === "remove") await api.tasks.remove(workspace.id, workflowId, task.id, task.revision);
      else await api.tasks.cancel(workspace.id, workflowId, task.id, task.revision);
    },
    onSuccess: (_, { action }) => {
      toast.success(action === "remove" ? "Task removed from the plan" : "Task cancelled");
      setConfirmTask(null);
      void refresh();
    },
    onError: (error) => {
      setConfirmTask(null);
      errors.handle(error);
    },
  });

  if (workflow.isPending) return <PageSkeleton />;
  if (workflow.isError) return <ErrorState error={workflow.error} onRetry={() => void workflow.refetch()} />;
  const wf = workflow.data;
  const taskList = tasks.data ?? [];
  const activeTasks = taskList.filter((t) => !t.removed);
  const planEditable = canEdit && PLAN_EDITABLE.has(wf.status);

  const onTaskAction = (action: TaskAction, task: Task) => {
    if (action === "edit") setTaskForm({ open: true, task });
    else if (action === "dependencies") setDependencyTask(task);
    else if (action === "assign") setAssignTask(task);
    else setConfirmTask({ action, task });
  };

  return (
    <div className="space-y-6">
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={wf.status} />
          {live ? (
            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground" aria-live="off">
              <RadioIcon className="size-3.5 animate-pulse" aria-hidden /> Live updates on
            </span>
          ) : null}
          <span className="text-xs text-muted-foreground">Revision {wf.revision}</span>
        </div>
        <h1 className="text-2xl font-semibold tracking-tight break-words whitespace-pre-wrap">{wf.prompt}</h1>
        <p className="text-sm text-muted-foreground">{WORKFLOW_STATUS_HELP[wf.status]}</p>
        {canEdit ? (
          <div className="flex flex-wrap gap-2">
            <WorkflowActions workflow={wf} onError={errors.handle} />
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">You have read-only access to this workflow.</p>
        )}
      </header>

      {planEditable && PLAN_EDIT_REOPENS.has(wf.status) ? (
        <Alert>
          <AlertDescription>
            Changing tasks or dependencies now sends the workflow back to planning
            {wf.autonomy_policy === "approval_required" ? " and the plan must be approved again" : ""}.
          </AlertDescription>
        </Alert>
      ) : null}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="max-w-full justify-start overflow-x-auto">
          <TabsTrigger value="tasks">Tasks ({activeTasks.length})</TabsTrigger>
          <TabsTrigger value="requirements">
            Requirements{wf.analysis_status === "PENDING" ? " (analysing…)" : ""}
          </TabsTrigger>
          <TabsTrigger value="plan">
            Plan draft{wf.plan_status === "PENDING" ? " (drafting…)" : ""}
          </TabsTrigger>
          <TabsTrigger value="graph">Dependencies</TabsTrigger>
          <TabsTrigger value="timeline">Timeline</TabsTrigger>
          <TabsTrigger value="details">Settings</TabsTrigger>
        </TabsList>

        <TabsContent value="tasks" className="space-y-4 pt-2">
          {planEditable ? (
            <Button onClick={() => setTaskForm({ open: true, task: null })}>
              <PlusIcon data-icon="inline-start" /> Add task
            </Button>
          ) : null}
          {tasks.isPending ? (
            <p className="text-sm text-muted-foreground">Loading tasks…</p>
          ) : tasks.isError ? (
            <ErrorState error={tasks.error} onRetry={() => void tasks.refetch()} />
          ) : taskList.length === 0 ? (
            <EmptyState
              title="No tasks yet"
              description={planEditable ? "Add the tasks that achieve this goal." : "This workflow has no tasks."}
            />
          ) : (
            <TaskList workflow={wf} tasks={taskList} agentsById={agentsById} canEdit={canEdit} onAction={onTaskAction} />
          )}
        </TabsContent>

        <TabsContent value="requirements" className="pt-2">
          <RequirementsPanel workflow={wf} onReviewPlan={() => setTab("plan")} />
        </TabsContent>

        <TabsContent value="plan" className="pt-2">
          <PlanDraftPreview
            workflow={wf}
            existingTaskCount={activeTasks.length}
            onConfirmed={() => setTab("tasks")}
            onConflict={errors.handle}
          />
        </TabsContent>

        <TabsContent value="graph" className="pt-2">
          <DependencyGraph tasks={taskList} />
        </TabsContent>

        <TabsContent value="timeline" className="pt-2">
          {events.isPending ? (
            <p className="text-sm text-muted-foreground">Loading events…</p>
          ) : events.isError ? (
            <ErrorState error={events.error} onRetry={() => void events.refetch()} />
          ) : (
            <EventTimeline events={events.data} tasks={taskList} />
          )}
        </TabsContent>

        <TabsContent value="details" className="pt-2">
          <WorkflowSettingsCard workflow={wf} />
        </TabsContent>
      </Tabs>

      <TaskFormDialog
        open={taskForm.open}
        task={taskForm.task}
        tasks={taskList}
        workflowId={wf.id}
        onOpenChange={(open) => setTaskForm((s) => ({ ...s, open }))}
        onSaved={() => void refresh()}
        onError={errors.handle}
      />
      <DependenciesDialog
        task={dependencyTask ? (taskList.find((t) => t.id === dependencyTask.id) ?? null) : null}
        tasks={taskList}
        workflowId={wf.id}
        onOpenChange={(open) => !open && setDependencyTask(null)}
        onChanged={() => void refresh()}
      />
      <AssignAgentDialog
        task={assignTask ? (taskList.find((t) => t.id === assignTask.id) ?? null) : null}
        workflowId={wf.id}
        onOpenChange={(open) => !open && setAssignTask(null)}
        onAssigned={() => void refresh()}
        onError={errors.handle}
      />
      <ConfirmDialog
        open={confirmTask !== null}
        onOpenChange={(open) => !open && setConfirmTask(null)}
        title={confirmTask?.action === "remove" ? "Remove this task?" : "Cancel this task?"}
        description={
          confirmTask?.action === "remove"
            ? `"${confirmTask.task.title}" is removed from the plan and kept as cancelled history.`
            : `"${confirmTask?.task.title ?? ""}" stops and will not run again. This cannot be undone.`
        }
        confirmLabel={confirmTask?.action === "remove" ? "Remove task" : "Cancel task"}
        destructive
        pending={taskChange.isPending}
        onConfirm={() => confirmTask && taskChange.mutate(confirmTask)}
      />
      <RevisionConflictDialog open={errors.conflict} subject="workflow" onReload={reloadAfterConflict} />
    </div>
  );
}

function WorkflowSettingsCard({ workflow: wf }: { workflow: Workflow }) {
  const rows: [string, React.ReactNode][] = [
    ["Plan approval", wf.autonomy_policy === "autonomous" ? "Autonomous" : "Approval required"],
    ["Budget limit", formatMoney(wf.budget_limit, wf.currency)],
    ["Budget spent", "Not measured yet (Phase 2)"],
    ["Currency", wf.currency],
    ["Maximum parallel tasks", wf.max_parallel_tasks],
    ["Agent proposals during run", wf.allow_dynamic_agent_proposals ? "Allowed" : "Not allowed"],
    ["Deadline", wf.deadline ? formatDateTime(wf.deadline) : "None"],
    ["Created", <RelativeTime key="c" value={wf.created_at} />],
    ["Started", wf.started_at ? <RelativeTime key="s" value={wf.started_at} /> : "Not started"],
    ["Finished", wf.completed_at ? <RelativeTime key="f" value={wf.completed_at} /> : "—"],
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Settings</h2>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[max-content_1fr]">
          {rows.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="font-medium">{value}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}
