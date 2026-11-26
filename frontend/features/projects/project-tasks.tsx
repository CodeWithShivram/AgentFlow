import { StatusBadge } from "@/components/common/status-badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { Task } from "@/lib/api/types";

const SCOPE_SHOWN = 3;

/** A compact task list: title, status, effort and file scope (plan text: plain text only). */
export function ProjectTasks({ tasks }: { tasks: Task[] }) {
  const active = tasks.filter((t) => !t.removed).sort((a, b) => b.priority - a.priority);
  if (active.length === 0) {
    return <p className="text-sm text-muted-foreground">No tasks yet. They appear once the plan is confirmed.</p>;
  }
  return (
    <div className="rounded-lg border">
      <Table aria-label="Project tasks">
        <TableHeader>
          <TableRow>
            <TableHead>Task</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Effort</TableHead>
            <TableHead className="hidden md:table-cell">Files</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {active.map((task) => {
            const scope = task.file_scope ?? [];
            return (
              <TableRow key={task.id}>
                <TableCell className="max-w-[14rem] whitespace-normal break-words sm:max-w-sm">{task.title}</TableCell>
                <TableCell>
                  <StatusBadge status={task.status} />
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {typeof task.effort_points === "number" ? (
                    task.effort_points
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="hidden max-w-xs whitespace-normal break-all text-xs text-muted-foreground md:table-cell">
                  {scope.length === 0
                    ? "—"
                    : scope.slice(0, SCOPE_SHOWN).join(", ") +
                      (scope.length > SCOPE_SHOWN ? ` +${scope.length - SCOPE_SHOWN} more` : "")}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
