"""Workflow and task state machines (ADR-0009). Pure data and checks; no database access.

Each allowed edge names the actor classes that may perform it. Everything not listed is
rejected. Guards that need data (dependencies, required tasks) are applied by the transition
service in app/services/transitions.py, which is the only code allowed to change a status.
"""

from enum import StrEnum

from app.models import TaskStatus as T
from app.models import WorkflowStatus as W


class Actor(StrEnum):
    USER = "USER"  # an authenticated person, through the API
    SCHEDULER = "SCHEDULER"  # assigns and dispatches tasks (Step 1.8+)
    WORKER = "WORKER"  # the agent runtime executing a task
    VERIFIER = "VERIFIER"  # platform verification (Phase 5)
    SYSTEM = "SYSTEM"  # platform bookkeeping: readiness, propagation, planner, policies


_U, _S, _K, _V, _Y = Actor.USER, Actor.SCHEDULER, Actor.WORKER, Actor.VERIFIER, Actor.SYSTEM
_PEOPLE_OR_SYSTEM = frozenset({_U, _Y})

WORKFLOW_TERMINAL = frozenset({W.COMPLETED, W.FAILED, W.CANCELLED})
TASK_TERMINAL = frozenset({T.COMPLETED, T.FAILED, T.CANCELLED})

WORKFLOW_TRANSITIONS: dict[tuple[W, W], frozenset[Actor]] = {
    (W.PENDING, W.PLANNING): frozenset({_U, _Y}),
    (W.PENDING, W.CANCELLED): _PEOPLE_OR_SYSTEM,
    (W.PENDING, W.FAILED): frozenset({_Y}),
    (W.PLANNING, W.AWAITING_APPROVAL): frozenset({_U, _Y}),
    (W.PLANNING, W.READY): frozenset({_U, _Y}),
    (W.PLANNING, W.FAILED): frozenset({_Y}),
    (W.PLANNING, W.CANCELLED): _PEOPLE_OR_SYSTEM,
    (W.AWAITING_APPROVAL, W.READY): frozenset({_U}),
    (W.AWAITING_APPROVAL, W.PLANNING): frozenset({_U, _Y}),
    (W.AWAITING_APPROVAL, W.CANCELLED): _PEOPLE_OR_SYSTEM,
    (W.AWAITING_APPROVAL, W.FAILED): frozenset({_Y}),
    (W.READY, W.RUNNING): frozenset({_U, _Y}),
    (W.READY, W.PLANNING): frozenset({_U, _Y}),
    (W.READY, W.CANCELLED): _PEOPLE_OR_SYSTEM,
    (W.RUNNING, W.PAUSED): frozenset({_U, _Y}),
    (W.RUNNING, W.BLOCKED): frozenset({_Y}),
    (W.RUNNING, W.COMPLETED): frozenset({_Y}),
    (W.RUNNING, W.FAILED): frozenset({_Y}),
    (W.RUNNING, W.CANCELLED): _PEOPLE_OR_SYSTEM,
    (W.PAUSED, W.RUNNING): frozenset({_U, _Y}),
    (W.PAUSED, W.CANCELLED): _PEOPLE_OR_SYSTEM,
    (W.BLOCKED, W.RUNNING): frozenset({_U, _Y}),
    (W.BLOCKED, W.PAUSED): frozenset({_U, _Y}),
    (W.BLOCKED, W.FAILED): frozenset({_Y}),
    (W.BLOCKED, W.CANCELLED): _PEOPLE_OR_SYSTEM,
}

TASK_TRANSITIONS: dict[tuple[T, T], frozenset[Actor]] = {
    (T.PENDING, T.READY): frozenset({_Y}),
    (T.PENDING, T.BLOCKED): frozenset({_Y}),
    (T.PENDING, T.CANCELLED): _PEOPLE_OR_SYSTEM,
    (T.READY, T.ASSIGNED): frozenset({_U, _S}),
    (T.READY, T.BLOCKED): frozenset({_Y}),
    (T.READY, T.CANCELLED): _PEOPLE_OR_SYSTEM,
    (T.ASSIGNED, T.QUEUED): frozenset({_S}),
    (T.ASSIGNED, T.READY): frozenset({_U, _S}),
    (T.ASSIGNED, T.CANCELLED): _PEOPLE_OR_SYSTEM,
    (T.QUEUED, T.IN_PROGRESS): frozenset({_K}),
    (T.QUEUED, T.ASSIGNED): frozenset({_S}),
    (T.QUEUED, T.CANCELLED): _PEOPLE_OR_SYSTEM,
    # The only edge an agent may cause besides starting: submitting a result.
    (T.IN_PROGRESS, T.SUBMITTED): frozenset({_K}),
    (T.IN_PROGRESS, T.WAITING_APPROVAL): frozenset({_K, _Y}),
    (T.IN_PROGRESS, T.REWORK_REQUIRED): frozenset({_Y}),
    (T.IN_PROGRESS, T.ESCALATED): frozenset({_Y}),
    (T.IN_PROGRESS, T.FAILED): frozenset({_Y}),
    (T.IN_PROGRESS, T.CANCELLED): _PEOPLE_OR_SYSTEM,
    (T.SUBMITTED, T.VERIFYING): frozenset({_V}),
    (T.SUBMITTED, T.CANCELLED): _PEOPLE_OR_SYSTEM,
    # Only platform verification accepts work (handoff 5.3).
    (T.VERIFYING, T.COMPLETED): frozenset({_V}),
    (T.VERIFYING, T.REWORK_REQUIRED): frozenset({_V, _Y}),
    (T.VERIFYING, T.WAITING_APPROVAL): frozenset({_V, _Y}),
    (T.VERIFYING, T.ESCALATED): frozenset({_V, _Y}),
    (T.VERIFYING, T.FAILED): frozenset({_V, _Y}),
    (T.REWORK_REQUIRED, T.ASSIGNED): frozenset({_U, _S}),
    (T.REWORK_REQUIRED, T.ESCALATED): frozenset({_Y}),
    (T.REWORK_REQUIRED, T.FAILED): frozenset({_Y}),
    (T.REWORK_REQUIRED, T.CANCELLED): _PEOPLE_OR_SYSTEM,
    (T.WAITING_APPROVAL, T.QUEUED): frozenset({_U}),
    (T.WAITING_APPROVAL, T.FAILED): frozenset({_U}),
    (T.WAITING_APPROVAL, T.CANCELLED): _PEOPLE_OR_SYSTEM,
    (T.BLOCKED, T.READY): frozenset({_Y}),
    (T.BLOCKED, T.PENDING): frozenset({_Y}),
    (T.BLOCKED, T.FAILED): frozenset({_Y}),
    (T.BLOCKED, T.CANCELLED): _PEOPLE_OR_SYSTEM,
    (T.ESCALATED, T.ASSIGNED): frozenset({_U}),
    (T.ESCALATED, T.FAILED): frozenset({_U, _Y}),
    (T.ESCALATED, T.CANCELLED): _PEOPLE_OR_SYSTEM,
}

# Workflow statuses in which the plan (tasks, dependencies) may be edited. Editing in
# AWAITING_APPROVAL or READY sends the workflow back to PLANNING (approval is invalidated).
PLAN_EDITABLE = frozenset({W.PENDING, W.PLANNING, W.AWAITING_APPROVAL, W.READY})
PLAN_EDIT_REOPENS = frozenset({W.AWAITING_APPROVAL, W.READY})

# Dispatch and execution need a RUNNING workflow: nothing is queued or started while the
# workflow is PAUSED or BLOCKED. Assignment is not dispatch, so an escalated task can be
# reassigned while BLOCKED, which is what lets a blocked workflow be resumed. In-flight results
# (SUBMITTED, VERIFYING, ...) may still be recorded while PAUSED or BLOCKED.
TASK_STATUSES_NEEDING_RUNNING_WORKFLOW = frozenset({T.QUEUED, T.IN_PROGRESS})
# Workflow statuses in which task execution transitions may happen at all.
WORKFLOW_EXECUTING = frozenset({W.RUNNING, W.PAUSED, W.BLOCKED})


def workflow_transition_allowed(source: W, target: W, actor: Actor) -> bool:
    return actor in WORKFLOW_TRANSITIONS.get((source, target), frozenset())


def task_transition_allowed(source: T, target: T, actor: Actor) -> bool:
    return actor in TASK_TRANSITIONS.get((source, target), frozenset())
