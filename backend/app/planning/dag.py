"""DAG validation of plan drafts (Phase 2, Step 2.4, ADR-0016). Pure functions, no I/O.

Errors make a draft unusable (generation retries them; confirmation will refuse them).
Warnings explain risks the user should see before confirming a draft.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

Severity = Literal["error", "warning"]


class DagTask(Protocol):
    @property
    def key(self) -> str: ...
    @property
    def depends_on(self) -> Sequence[str]: ...
    @property
    def required(self) -> bool: ...
    @property
    def required_skills(self) -> Sequence[str]: ...


@dataclass(frozen=True)
class PlanIssue:
    code: str
    severity: Severity
    message: str
    task_keys: tuple[str, ...] = ()
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DagReport:
    # Stage n holds the tasks whose longest dependency chain has n predecessors; tasks in one
    # stage can run in parallel. Empty when the graph has a cycle.
    stages: list[list[str]]
    issues: list[PlanIssue]

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)


def find_cycle(tasks: Sequence[DagTask]) -> list[str]:
    """Keys of the tasks on or behind a dependency cycle (Kahn's algorithm); empty if acyclic.
    Dependencies on unknown keys are ignored here; the schema rejects them."""
    keys = {task.key for task in tasks}
    remaining = {task.key: {d for d in task.depends_on if d in keys} for task in tasks}
    ready = [key for key, deps in remaining.items() if not deps]
    while ready:
        done = ready.pop()
        del remaining[done]
        for key, deps in remaining.items():
            if done in deps:
                deps.discard(done)
                if not deps:
                    ready.append(key)
    return sorted(remaining)


def stages(tasks: Sequence[DagTask]) -> list[list[str]]:
    """Longest-path levels of an acyclic plan, each stage in plan order."""
    by_key = {task.key: task for task in tasks}
    level: dict[str, int] = {}

    def depth(key: str) -> int:
        if key not in level:
            deps = [d for d in by_key[key].depends_on if d in by_key]
            level[key] = 1 + max((depth(d) for d in deps), default=-1)
        return level[key]

    result: list[list[str]] = []
    for task in tasks:
        index = depth(task.key)
        while len(result) <= index:
            result.append([])
        result[index].append(task.key)
    return result


def analyse_plan(
    tasks: Sequence[DagTask],
    *,
    max_parallel_tasks: int,
    available_skills: Collection[str] | None = None,
) -> DagReport:
    """Validates a plan's dependency graph and lists what the user should know.

    `available_skills` is the union of capabilities of the workspace's ACTIVE agents; when given,
    skills nobody provides are reported (a task needing one could never be assigned).
    """
    issues: list[PlanIssue] = []
    cycle = find_cycle(tasks)
    if cycle:
        issues.append(
            PlanIssue(
                code="DEPENDENCY_CYCLE",
                severity="error",
                message="These tasks depend on each other in a cycle, so none of them could start.",
                task_keys=tuple(cycle),
            )
        )
    by_key = {task.key: task for task in tasks}
    for task in tasks:
        optional = sorted(
            d for d in task.depends_on if d in by_key and task.required and not by_key[d].required
        )
        if optional:
            issues.append(
                PlanIssue(
                    code="REQUIRED_DEPENDS_ON_OPTIONAL",
                    severity="warning",
                    message=(
                        "A required task waits for optional tasks; if those are skipped, it "
                        "cannot start."
                    ),
                    task_keys=(task.key, *optional),
                )
            )
    if available_skills is not None:
        missing = sorted(
            {skill for task in tasks for skill in task.required_skills} - set(available_skills)
        )
        for skill in missing:
            needing = tuple(t.key for t in tasks if skill in t.required_skills)
            issues.append(
                PlanIssue(
                    code="SKILL_NOT_AVAILABLE",
                    severity="warning",
                    message=f"No active agent in this workspace has the skill “{skill}”.",
                    task_keys=needing,
                    details={"skill": skill},
                )
            )
    plan_stages = [] if cycle else stages(tasks)
    for index, stage in enumerate(plan_stages):
        if len(stage) > max_parallel_tasks:
            issues.append(
                PlanIssue(
                    code="STAGE_EXCEEDS_PARALLEL_LIMIT",
                    severity="warning",
                    message=(
                        f"Stage {index + 1} has {len(stage)} tasks but at most "
                        f"{max_parallel_tasks} run at once; the rest will wait."
                    ),
                    task_keys=tuple(stage),
                    details={"stage": index + 1, "limit": max_parallel_tasks},
                )
            )
    return DagReport(stages=plan_stages, issues=issues)
