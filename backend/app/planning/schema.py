"""Plan draft schema (Phase 2, Steps 2.3-2.4, ADR-0015, ADR-0016).

Shared by the planning job (worker) and the plan preview (API); it imports no gateway code.
A plan must also be an acyclic graph: a cyclic model reply is invalid output and is retried.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.planning.dag import find_cycle
from app.schemas.agents import Capability, SafeText, Slug
from app.schemas.workflows import AcceptanceCriterion

MAX_TASKS = 25

Text = Annotated[SafeText, StringConstraints(min_length=1, max_length=2000, strip_whitespace=True)]
Title = Annotated[SafeText, StringConstraints(min_length=1, max_length=300, strip_whitespace=True)]
_STRICT = ConfigDict(extra="forbid", frozen=True)


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

    @model_validator(mode="after")
    def _unique_entries(self) -> "PlannedTask":
        if len(set(self.required_skills)) != len(self.required_skills):
            raise ValueError("required_skills must be unique")
        ids = [c.id for c in self.acceptance_criteria]
        if len(set(ids)) != len(ids):
            raise ValueError("acceptance criterion ids must be unique")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("depends_on must not repeat a task")
        if self.key in self.depends_on:
            raise ValueError("a task cannot depend on itself")
        return self


class PlanProposal(BaseModel):
    """What the model must return, and what a workflow_plans draft stores."""

    model_config = _STRICT

    schema_version: int = Field(default=1, ge=1, le=1)
    summary: Text
    tasks: Annotated[list[PlannedTask], Field(min_length=1, max_length=MAX_TASKS)]
    risks: Annotated[list[Text], Field(max_length=10)] = []

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
