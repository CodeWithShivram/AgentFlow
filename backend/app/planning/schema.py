"""Plan draft schema (Phase 2, Steps 2.3-2.4, ADR-0015, ADR-0016).

Shared by the planning job (worker) and the plan preview (API); it imports no gateway code.
A plan must also be an acyclic graph: a cyclic model reply is invalid output and is retried.

Schema version 2 (ADR-0048) is the software project plan: it adds a `project` (name, stack
profile, architecture, assumptions) and, per task, a `file_scope` the task owns and an
`effort_points` estimate. Scopes of different tasks never overlap (so each of the profile's
shared files has at most one owner; a first plan must own them all, see `unowned_shared_files`).
Version 1 plans have neither.
"""

from typing import Annotated, Any, Literal

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
# Only the Developer runtime proposes files (ADR-0022, ADR-0049), so every task of a software
# project must need nothing but its skills; other runtimes return text that cannot be applied.
PROJECT_TASK_SKILLS = frozenset({"code_generation", "implementation", "debugging"})

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


UiTarget = Annotated[
    SafeText, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)
]
MAX_UI_FLOWS = 3
MAX_UI_STEPS = 10


class UiStep(BaseModel):
    """One step of a browser flow (ADR-0062): click a button or link by its visible name, fill a
    field by its label, or expect a visible text."""

    model_config = _STRICT

    action: Literal["click", "fill", "expect_text"]
    target: UiTarget
    value: UiTarget | None = None

    @model_validator(mode="after")
    def _value_only_for_fill(self) -> "UiStep":
        if (self.action == "fill") != (self.value is not None):
            raise ValueError("a fill step needs a value, and only a fill step has one")
        return self


class UiFlow(BaseModel):
    """A short user journey the web profile's browser check runs on the finished page."""

    model_config = _STRICT

    name: UiTarget
    steps: Annotated[list[UiStep], Field(min_length=1, max_length=MAX_UI_STEPS)]


class ProjectSpec(BaseModel):
    """The software project a version 2 plan builds (ADR-0048)."""

    model_config = _STRICT

    name: Slug
    stack_profile: Annotated[str, AfterValidator(_known_profile)]
    architecture: Text
    assumptions: Annotated[list[Text], Field(max_length=10)] = []
    # Web profiles only (ADR-0062): flows the browser check runs on the finished page.
    ui_flows: Annotated[list[UiFlow], Field(max_length=MAX_UI_FLOWS)] = []

    @model_validator(mode="after")
    def _flows_need_a_browser_check(self) -> "ProjectSpec":
        if self.ui_flows and PROFILES[self.stack_profile].preview is None:
            raise ValueError("ui_flows need a web stack profile")
        return self


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

    @model_validator(mode="before")
    @classmethod
    def _specialist_skills_replace_the_developers(cls, data: Any) -> Any:
        """A project task that needs a profile's extra skill (ADR-0057: `ui_development`) needs
        only those skills. The specialist is a Developer runtime, so it writes the files anyway,
        and listing the Developer's skills as well would leave the task with no eligible agent
        (a live Gemini plan asked for `ui_development` and `code_generation` together)."""
        if not isinstance(data, dict):
            return data
        project, tasks = data.get("project"), data.get("tasks")
        if not isinstance(project, dict) or not isinstance(tasks, list):
            return data
        profile = PROFILES.get(str(project.get("stack_profile")))
        if profile is None or not profile.extra_task_skills:
            return data
        extras = set(profile.extra_task_skills)
        normalized = []
        for task in tasks:
            skills = task.get("required_skills") if isinstance(task, dict) else None
            if isinstance(skills, list) and extras & set(skills):
                task = {**task, "required_skills": [s for s in skills if s in extras]}
            normalized.append(task)
        return {**data, "tasks": normalized}

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
        allowed = PROJECT_TASK_SKILLS | set(PROFILES[self.project.stack_profile].extra_task_skills)
        for task in self.tasks:
            outside = sorted(set(task.required_skills) - allowed)
            if outside:
                raise ValueError(
                    f"task {task.key} of a software project may only require "
                    f"{', '.join(sorted(allowed))} (not {', '.join(outside)})"
                )
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


class ProjectPlannedTask(PlannedTask):
    """A software project task: its file scope and effort are required in the schema the model
    sees (a live Gemini run often left effort_points out while they were optional there)."""

    file_scope: Annotated[list[ScopeEntry], Field(min_length=1, max_length=MAX_SCOPE_ENTRIES)]
    effort_points: int = Field(ge=1, le=MAX_EFFORT_POINTS)


class ProjectPlanProposal(PlanProposal):
    """What the model must return for a software project: a version 2 plan whose `project`, and
    every task's scope and effort, are required in the schema the model sees, not only by the
    cross-field rules (live Gemini runs left them out while they were optional)."""

    schema_version: Literal[2] = 2
    project: ProjectSpec
    # Narrowed to project tasks; lists are invariant for mypy, but every item is a PlannedTask.
    tasks: Annotated[list[ProjectPlannedTask], Field(min_length=1, max_length=MAX_TASKS)]  # type: ignore[assignment]
