"""Plan draft schema (Phase 2, Steps 2.3-2.4, ADR-0015, ADR-0016).

Shared by the planning job (worker) and the plan preview (API); it imports no gateway code.
A plan must also be an acyclic graph: a cyclic model reply is invalid output and is retried.

Schema version 2 (ADR-0048) is the software project plan: it adds a `project` (name, stack
profile, architecture, assumptions) and, per task, a `file_scope` the task owns and an
`effort_points` estimate. Scopes of different tasks never overlap (so each of the profile's
shared files has at most one owner; a first plan must own them all, see `unowned_shared_files`).
Version 1 plans have neither.
"""

from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.planning.dag import find_cycle
from app.projects.profiles import PROFILES
from app.projects.scope import covers, normalize_entry, overlaps
from app.schemas.agents import Capability, SafeText, Slug
from app.schemas.workflows import AcceptanceCriterion

MAX_TASKS = 25
MAX_SCOPE_ENTRIES = 50
MAX_EFFORT_POINTS = 13

Text = Annotated[SafeText, StringConstraints(min_length=1, max_length=2000, strip_whitespace=True)]
Title = Annotated[SafeText, StringConstraints(min_length=1, max_length=300, strip_whitespace=True)]
ScopeEntry = Annotated[
    str, StringConstraints(min_length=1, max_length=300), AfterValidator(normalize_entry)
]
_STRICT = ConfigDict(extra="forbid", frozen=True)


def _known_profile(value: str) -> str:
    if value not in PROFILES:
        raise ValueError(f"stack_profile must be one of: {', '.join(sorted(PROFILES))}")
    return value


class ProjectSpec(BaseModel):
    """The software project a version 2 plan builds (ADR-0048)."""

    model_config = _STRICT

    name: Slug
    stack_profile: Annotated[str, AfterValidator(_known_profile)]
    architecture: Text
    assumptions: Annotated[list[Text], Field(max_length=10)] = []


class PlannedTask(BaseModel):
    """One small, verifiable unit of work. Fields fit TaskCreateRequest, so a confirmed draft
    can become workflow tasks unchanged (Step 2.5)."""

    model_config = _STRICT

    key: Slug
    title: Title
    description: Annotated[SafeText, StringConstraints(max_length=4000)] = ""
    required_skills: Annotated[list[Capability], Field(min_length=1, max_length=10)]
    deliverables: Annotated[list[Text], Field(min_length=1, max_length=10)]
    acceptance_criteria: Annotated[list[AcceptanceCriterion], Field(min_length=1, max_length=20)]
    priority: int = Field(default=0, ge=-100, le=100)
    required: bool = True
    depends_on: Annotated[list[Slug], Field(max_length=MAX_TASKS - 1)] = []
    # Replan drafts only (ADR-0033): keys of the workflow's completed tasks this task builds on,
    # as listed in the replan request. Checked against the draft's basis, not here.
    depends_on_completed: Annotated[list[Slug], Field(max_length=MAX_TASKS)] = []
    # Version 2 only (ADR-0048): the files and folders this task alone may write, and its
    # estimated effort (1 = trivial ... 13 = the largest task worth keeping whole).
    file_scope: Annotated[list[ScopeEntry], Field(max_length=MAX_SCOPE_ENTRIES)] = []
    effort_points: int | None = Field(default=None, ge=1, le=MAX_EFFORT_POINTS)

    @model_validator(mode="after")
    def _unique_entries(self) -> "PlannedTask":
        if len(set(self.required_skills)) != len(self.required_skills):
            raise ValueError("required_skills must be unique")
        ids = [c.id for c in self.acceptance_criteria]
        if len(set(ids)) != len(ids):
            raise ValueError("acceptance criterion ids must be unique")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("depends_on must not repeat a task")
        if len(set(self.depends_on_completed)) != len(self.depends_on_completed):
            raise ValueError("depends_on_completed must not repeat a task")
        if self.key in self.depends_on:
            raise ValueError("a task cannot depend on itself")
        for index, entry in enumerate(self.file_scope):
            if any(overlaps(entry, other) for other in self.file_scope[index + 1 :]):
                raise ValueError("file_scope entries must not overlap")
        return self


class PlanProposal(BaseModel):
    """What the model must return, and what a workflow_plans draft stores."""

    model_config = _STRICT

    schema_version: int = Field(default=1, ge=1, le=2)
    summary: Text
    tasks: Annotated[list[PlannedTask], Field(min_length=1, max_length=MAX_TASKS)]
    risks: Annotated[list[Text], Field(max_length=10)] = []
    # Version 2 only: the software project (ADR-0048).
    project: ProjectSpec | None = None

    @model_validator(mode="after")
    def _project_plan(self) -> "PlanProposal":
        if self.project is None:
            if self.schema_version != 1:
                raise ValueError("a version 2 plan needs a project")
            if any(t.file_scope or t.effort_points is not None for t in self.tasks):
                raise ValueError("file_scope and effort_points belong to project plans only")
            return self
        if self.schema_version != 2:
            raise ValueError("a project plan has schema_version 2")
        for task in self.tasks:
            if not task.file_scope:
                raise ValueError(f"task {task.key} needs a file_scope")
            if task.effort_points is None:
                raise ValueError(f"task {task.key} needs effort_points")
        for index, task in enumerate(self.tasks):
            for other in self.tasks[index + 1 :]:
                if any(overlaps(a, b) for a in task.file_scope for b in other.file_scope):
                    raise ValueError(f"tasks {task.key} and {other.key} own overlapping files")
        return self

    def unowned_shared_files(self) -> list[str]:
        """The profile's shared files no task owns. A first plan must own them all; a replan
        may leave them with the completed tasks that already own them (ADR-0048)."""
        if self.project is None:
            return []
        return [
            shared
            for shared in PROFILES[self.project.stack_profile].shared_files
            if not any(covers(e, shared) for t in self.tasks for e in t.file_scope)
        ]

    @model_validator(mode="after")
    def _references(self) -> "PlanProposal":
        keys = [task.key for task in self.tasks]
        if len(set(keys)) != len(keys):
            raise ValueError("task keys must be unique")
        known = set(keys)
        for task in self.tasks:
            unknown = sorted(set(task.depends_on) - known)
            if unknown:
                raise ValueError(f"task {task.key} depends on unknown tasks: {', '.join(unknown)}")
        cycle = find_cycle(self.tasks)
        if cycle:
            raise ValueError(f"dependency cycle among tasks: {', '.join(cycle)}")
        return self
