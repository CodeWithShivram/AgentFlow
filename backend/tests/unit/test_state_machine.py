"""Every workflow and task transition, allowed and rejected, against the approved matrix (OD-11).

The expected edges are written out here independently of app/workflows/state_machine.py, so a
change to either one without the other fails.
"""

import itertools
import uuid

import pytest

from app.models import TaskStatus as T
from app.models import WorkflowStatus as W
from app.services.tasks import would_create_cycle
from app.workflows.state_machine import (
    TASK_TERMINAL,
    TASK_TRANSITIONS,
    WORKFLOW_TERMINAL,
    WORKFLOW_TRANSITIONS,
    Actor,
    task_transition_allowed,
    workflow_transition_allowed,
)

APPROVED_WORKFLOW_EDGES = {
    W.PENDING: {W.PLANNING, W.CANCELLED, W.FAILED},
    W.PLANNING: {W.AWAITING_APPROVAL, W.READY, W.FAILED, W.CANCELLED},
    W.AWAITING_APPROVAL: {W.READY, W.PLANNING, W.CANCELLED, W.FAILED},
    W.READY: {W.RUNNING, W.PLANNING, W.CANCELLED},
    W.RUNNING: {W.PAUSED, W.BLOCKED, W.COMPLETED, W.FAILED, W.CANCELLED},
    W.PAUSED: {W.RUNNING, W.CANCELLED},
    W.BLOCKED: {W.RUNNING, W.PAUSED, W.FAILED, W.CANCELLED},
    W.COMPLETED: set(),
    W.FAILED: set(),
    W.CANCELLED: set(),
}
APPROVED_TASK_EDGES = {
    T.PENDING: {T.READY, T.BLOCKED, T.CANCELLED},
    T.READY: {T.ASSIGNED, T.BLOCKED, T.CANCELLED},
    T.ASSIGNED: {T.QUEUED, T.READY, T.CANCELLED},
    T.QUEUED: {T.IN_PROGRESS, T.ASSIGNED, T.CANCELLED},
    T.IN_PROGRESS: {
        T.SUBMITTED,
        T.WAITING_APPROVAL,
        T.REWORK_REQUIRED,
        T.ESCALATED,
        T.FAILED,
        T.CANCELLED,
    },
    T.SUBMITTED: {T.VERIFYING, T.CANCELLED},
    T.VERIFYING: {T.COMPLETED, T.REWORK_REQUIRED, T.WAITING_APPROVAL, T.ESCALATED, T.FAILED},
    T.REWORK_REQUIRED: {T.ASSIGNED, T.ESCALATED, T.FAILED, T.CANCELLED},
    T.WAITING_APPROVAL: {T.QUEUED, T.FAILED, T.CANCELLED},
    T.BLOCKED: {T.READY, T.PENDING, T.FAILED, T.CANCELLED},
    T.ESCALATED: {T.ASSIGNED, T.FAILED, T.CANCELLED},
    T.COMPLETED: set(),
    T.FAILED: set(),
    T.CANCELLED: set(),
}


@pytest.mark.parametrize(("source", "target"), list(itertools.product(W, W)))
def test_workflow_transition_matrix(source: W, target: W) -> None:
    allowed_by_anyone = any(workflow_transition_allowed(source, target, a) for a in Actor)

    assert allowed_by_anyone == (target in APPROVED_WORKFLOW_EDGES[source])


@pytest.mark.parametrize(("source", "target"), list(itertools.product(T, T)))
def test_task_transition_matrix(source: T, target: T) -> None:
    allowed_by_anyone = any(task_transition_allowed(source, target, a) for a in Actor)

    assert allowed_by_anyone == (target in APPROVED_TASK_EDGES[source])


def test_terminal_states_never_change() -> None:
    assert WORKFLOW_TERMINAL == {W.COMPLETED, W.FAILED, W.CANCELLED}
    assert TASK_TERMINAL == {T.COMPLETED, T.FAILED, T.CANCELLED}
    assert not any(source in WORKFLOW_TERMINAL for source, _ in WORKFLOW_TRANSITIONS)
    assert not any(source in TASK_TERMINAL for source, _ in TASK_TRANSITIONS)


def test_only_the_verifier_can_complete_a_task() -> None:
    completers = {
        actor
        for (source, target), actors in TASK_TRANSITIONS.items()
        if target == T.COMPLETED
        for actor in actors
    }

    assert completers == {Actor.VERIFIER}


def test_agents_can_only_start_submit_or_request_approval() -> None:
    worker_edges = {edge for edge, actors in TASK_TRANSITIONS.items() if Actor.WORKER in actors}

    assert worker_edges == {
        (T.QUEUED, T.IN_PROGRESS),
        (T.IN_PROGRESS, T.SUBMITTED),
        (T.IN_PROGRESS, T.WAITING_APPROVAL),
    }


def test_users_cannot_complete_fail_or_block_workflows_directly() -> None:
    for target in (W.COMPLETED, W.FAILED, W.BLOCKED):
        assert not any(workflow_transition_allowed(s, target, Actor.USER) for s in W)


def test_users_can_cancel_every_unfinished_workflow_and_task() -> None:
    for status in set(W) - WORKFLOW_TERMINAL:
        assert workflow_transition_allowed(status, W.CANCELLED, Actor.USER), status
    for task_status in set(T) - TASK_TERMINAL - {T.VERIFYING}:
        assert task_transition_allowed(task_status, T.CANCELLED, Actor.USER), task_status


def test_plan_approval_is_a_user_decision() -> None:
    assert workflow_transition_allowed(W.AWAITING_APPROVAL, W.READY, Actor.USER)
    assert not workflow_transition_allowed(W.AWAITING_APPROVAL, W.READY, Actor.SYSTEM)


def test_readiness_is_computed_by_the_platform() -> None:
    assert task_transition_allowed(T.PENDING, T.READY, Actor.SYSTEM)
    assert not task_transition_allowed(T.PENDING, T.READY, Actor.USER)


# --- DAG cycle detection ---------------------------------------------------------------------

A, B, C, D = (uuid.uuid4() for _ in range(4))


@pytest.mark.parametrize(
    ("edges", "new_edge", "cycle"),
    [
        ([], (A, B), False),
        ([(A, B)], (B, A), True),  # two-node cycle
        ([(A, B), (B, C)], (C, A), True),  # three-node cycle
        ([(A, B), (B, C), (C, D)], (D, A), True),
        ([(A, B), (A, C), (B, D), (C, D)], (A, D), False),  # diamond plus a shortcut is fine
        ([(A, B), (C, D)], (B, C), False),
        ([(A, B), (C, D)], (D, A), False),
        ([(A, B), (B, C)], (A, C), False),
    ],
)
def test_cycle_detection(
    edges: list[tuple[uuid.UUID, uuid.UUID]], new_edge: tuple[uuid.UUID, uuid.UUID], cycle: bool
) -> None:
    assert would_create_cycle(edges, *new_edge) is cycle
