"""Parallel and sequential execution across whole dependency graphs (Step 4.6).

Each scenario drives a workflow from start to completion against real PostgreSQL: the scheduler
dispatches, a worker claims as the restricted worker role, the agent runs (MockAgent, no model),
and the platform's verification job (Step 5.1) completes each submitted task. Together they
check the two scheduling promises (handoff sections 6 and 15): independent tasks run at the same
time up to `max_parallel_tasks`, and a task never starts before its dependencies complete.
"""

import random
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.mock import MockAgent
from app.db.sync_session import session_scope
from app.execution import outbox, relay, runner
from app.llm.gateway import ModelGateway
from app.llm.mock import MockModelAdapter
from app.llm.types import AdapterResult, ModelRequest
from app.models import (
    Agent,
    AgentExecution,
    AgentStatus,
    AgentVersion,
    AutonomyPolicy,
    ExecutionStatus,
    OutboxEvent,
    TaskDependency,
    TaskStatus,
    User,
    Workflow,
    WorkflowStatus,
    WorkflowTask,
    Workspace,
)
from app.services.usage import record_model_call
from app.verification import job as verification_job
from app.workflows import engine
from app.workflows.state_machine import Actor
from tests.conftest import make_settings

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
# MockAgent never calls a model; the in-process scenarios run it explicitly.
_NO_MODEL = ModelGateway(
    make_settings(), MockModelAdapter(defaults={}), lambda record: uuid.uuid4()
)
MOCK_CONFIG = {
    "schema_version": 1,
    "system_instruction_ref": "workspace://prompts/w/v1",
    "capabilities": ["report_writing"],
    "tools": [],
}
WRITER_CONFIG = {**MOCK_CONFIG, "system_instruction_ref": "builtin://writer/v1"}


@dataclass(frozen=True)
class Graph:
    workspace_id: uuid.UUID
    workflow_id: uuid.UUID
    task_ids: list[uuid.UUID]
    max_parallel: int
    # (dependent, prerequisite) task indexes
    edges: list[tuple[int, int]]

    def prerequisites(self, task_id: uuid.UUID) -> list[uuid.UUID]:
        index = self.task_ids.index(task_id)
        return [self.task_ids[p] for d, p in self.edges if d == index]


def _drop_side_requests(s: Session, workflow_id: uuid.UUID) -> None:
    """These scenarios exercise task dispatch only; analysis and orchestration requests are
    covered by their own tests."""
    s.execute(
        delete(OutboxEvent).where(
            OutboxEvent.workflow_id == workflow_id,
            OutboxEvent.event_type.in_([outbox.WORKFLOW_ANALYZE, outbox.WORKFLOW_ORCHESTRATE]),
        )
    )


def _start_graph(
    admin: Factory,
    *,
    tasks: int,
    edges: list[tuple[int, int]],
    max_parallel: int,
    priorities: list[int] | None = None,
    configuration: dict[str, Any] = MOCK_CONFIG,
) -> Graph:
    """Starts a workflow whose tasks are all pre-assigned (ADR-0028), so from here on the
    scheduler alone decides what runs when. `edges` are (dependent, prerequisite) indexes."""
    assert all(p < d for d, p in edges), "edges must point to earlier tasks (acyclic)"
    with session_scope(admin) as s:
        user = User(email=f"{uuid.uuid4().hex[:8]}@example.com", password_hash="h")
        s.add(user)
        s.flush()
        workspace = Workspace(owner_id=user.id, name="W")
        s.add(workspace)
        s.flush()
        agent = Agent(workspace_id=workspace.id, name="Writer X", category="writing")
        s.add(agent)
        s.flush()
        version = AgentVersion(agent_id=agent.id, version=1, configuration=configuration)
        s.add(version)
        s.flush()
        agent.active_version_id, agent.status = version.id, AgentStatus.ACTIVE
        workflow = Workflow(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            prompt="p",
            autonomy_policy=AutonomyPolicy.AUTONOMOUS,
            max_parallel_tasks=max_parallel,
        )
        s.add(workflow)
        s.flush()
        rows = [
            WorkflowTask(
                workspace_id=workspace.id,
                workflow_id=workflow.id,
                title=f"t{i}",
                description="d",
                required_skills=["report_writing"],
                priority=priorities[i] if priorities else 0,
                assigned_agent_id=agent.id,
                assigned_agent_version_id=version.id,
            )
            for i in range(tasks)
        ]
        s.add_all(rows)
        s.flush()
        s.add_all(
            TaskDependency(
                task_id=rows[d].id, depends_on_task_id=rows[p].id, workflow_id=workflow.id
            )
            for d, p in edges
        )
        s.flush()
        for target in (WorkflowStatus.PLANNING, WorkflowStatus.READY, WorkflowStatus.RUNNING):
            engine.transition_workflow(s, workflow, target, actor=Actor.USER)
        _drop_side_requests(s, workflow.id)
        s.commit()
        return Graph(workspace.id, workflow.id, [t.id for t in rows], max_parallel, edges)


def _undelivered(admin: Factory, graph: Graph, delivered: set[uuid.UUID]) -> list[OutboxEvent]:
    with session_scope(admin) as s:
        events = s.execute(
            select(OutboxEvent)
            .where(
                OutboxEvent.workflow_id == graph.workflow_id,
                OutboxEvent.event_type == outbox.TASK_DISPATCH,
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
        ).scalars()
        return [e for e in events if e.id not in delivered]


def _claim(worker: Factory, event: OutboxEvent) -> runner.Claim | runner.Skipped:
    with session_scope(worker) as s:
        return runner.claim(
            s,
            event_id=event.id,
            workspace_id=event.workspace_id,
            workflow_id=uuid.UUID(event.payload["workflow_id"]),
            task_id=uuid.UUID(event.payload["task_id"]),
            agent_version_id=uuid.UUID(event.payload["agent_version_id"]),
        )


def _submit(worker: Factory, claim: runner.Claim) -> None:
    outcome = runner.run_agent(claim, MockAgent, models=_NO_MODEL, is_current=lambda: True)
    with session_scope(worker) as s:
        assert runner.finish(s, claim, outcome) == "submitted"


def _verify(admin: Factory, worker: Factory, graph: Graph, task_id: uuid.UUID) -> None:
    """Runs the platform's verification job (ADR-0030) on the task's verification request, as a
    worker would: SUBMITTED -> VERIFYING -> COMPLETED."""
    with session_scope(admin) as s:
        event = s.execute(
            select(OutboxEvent)
            .where(OutboxEvent.task_id == task_id, OutboxEvent.event_type == outbox.TASK_VERIFY)
            .order_by(OutboxEvent.created_at.desc())
            .limit(1)
        ).scalar_one()
    with session_scope(worker) as s:
        outcome = verification_job.run(
            s,
            event_id=event.id,
            workspace_id=graph.workspace_id,
            workflow_id=graph.workflow_id,
            task_id=task_id,
        )
    assert outcome == "passed"
    with session_scope(admin) as s:
        _drop_side_requests(s, graph.workflow_id)
        s.commit()


def _statuses(admin: Factory, graph: Graph) -> dict[uuid.UUID, TaskStatus]:
    with session_scope(admin) as s:
        rows = s.execute(
            select(WorkflowTask.id, WorkflowTask.status).where(
                WorkflowTask.workflow_id == graph.workflow_id
            )
        )
        return {row[0]: row[1] for row in rows}


def _workflow_status(admin: Factory, graph: Graph) -> WorkflowStatus:
    with session_scope(admin) as s:
        return s.execute(
            select(Workflow.status).where(Workflow.id == graph.workflow_id)
        ).scalar_one()


def _assert_finished_once_each(admin: Factory, graph: Graph) -> None:
    assert _workflow_status(admin, graph) == WorkflowStatus.COMPLETED
    assert set(_statuses(admin, graph).values()) == {TaskStatus.COMPLETED}
    with session_scope(admin) as s:
        counts = dict(
            s.execute(
                select(AgentExecution.task_id, func.count())
                .where(
                    AgentExecution.workflow_id == graph.workflow_id,
                    AgentExecution.status == ExecutionStatus.SUCCEEDED,
                )
                .group_by(AgentExecution.task_id)
            ).all()
        )
    assert counts == dict.fromkeys(graph.task_ids, 1)


def _run_in_rounds(admin: Factory, worker: Factory, graph: Graph) -> list[set[uuid.UUID]]:
    """Each round delivers every dispatch written so far and claims what it can: the attempts
    claimed in one round run at the same time. Then each is submitted and verified. Returns the
    tasks started per round."""
    delivered: set[uuid.UUID] = set()
    rounds: list[set[uuid.UUID]] = []
    for _ in range(len(graph.task_ids) + 1):  # every round starts at least one task
        claims = []
        for event in _undelivered(admin, graph, delivered):
            delivered.add(event.id)
            claim = _claim(worker, event)
            if isinstance(claim, runner.Claim):
                claims.append(claim)
        if not claims:
            break
        rounds.append({c.task_id for c in claims})
        for claim in claims:
            _submit(worker, claim)
            _verify(admin, worker, graph, claim.task_id)
    return rounds


# --- named scenarios ------------------------------------------------------------------------------


def test_a_chain_runs_one_task_at_a_time_in_dependency_order(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    graph = _start_graph(admin_factory, tasks=3, edges=[(1, 0), (2, 1)], max_parallel=3)
    a, b, c = graph.task_ids

    assert _run_in_rounds(admin_factory, worker_factory, graph) == [{a}, {b}, {c}]
    _assert_finished_once_each(admin_factory, graph)


def test_a_diamond_runs_its_independent_middle_tasks_in_parallel(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    # A, then B and C (both need only A), then D (needs B and C).
    graph = _start_graph(
        admin_factory, tasks=4, edges=[(1, 0), (2, 0), (3, 1), (3, 2)], max_parallel=3
    )
    a, b, c, d = graph.task_ids

    assert _run_in_rounds(admin_factory, worker_factory, graph) == [{a}, {b, c}, {d}]
    _assert_finished_once_each(admin_factory, graph)


def test_a_fan_out_wider_than_the_limit_runs_in_priority_order(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    graph = _start_graph(
        admin_factory, tasks=5, edges=[], max_parallel=2, priorities=[1, 5, 3, 4, 2]
    )
    p1, p5, p3, p4, p2 = graph.task_ids

    assert _run_in_rounds(admin_factory, worker_factory, graph) == [{p5, p4}, {p3, p2}, {p1}]
    _assert_finished_once_each(admin_factory, graph)


def test_resuming_dispatches_the_highest_priority_tasks_within_the_limit(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    # A and D start; B and C (higher priority) wait for A.
    graph = _start_graph(
        admin_factory,
        tasks=4,
        edges=[(1, 0), (2, 0)],
        max_parallel=2,
        priorities=[0, 9, 9, 0],
    )
    a, b, c, d = graph.task_ids
    started = {e.task_id: e for e in _undelivered(admin_factory, graph, set())}
    assert set(started) == {a, d}
    first_a, first_d = started[a], started[d]
    claim = _claim(worker_factory, first_a)
    assert isinstance(claim, runner.Claim)
    _submit(worker_factory, claim)
    with session_scope(admin_factory) as s:
        workflow = s.execute(
            select(Workflow).where(Workflow.id == graph.workflow_id).with_for_update()
        ).scalar_one()
        engine.transition_workflow(s, workflow, WorkflowStatus.PAUSED, actor=Actor.USER)
        s.commit()
    assert _claim(worker_factory, first_d) == runner.Skipped.NOT_RUNNING
    _verify(admin_factory, worker_factory, graph, a)  # completed while paused: B and C wait
    seen = {e.id for e in _undelivered(admin_factory, graph, set())}

    with session_scope(admin_factory) as s:
        workflow = s.execute(
            select(Workflow).where(Workflow.id == graph.workflow_id).with_for_update()
        ).scalar_one()
        engine.transition_workflow(s, workflow, WorkflowStatus.RUNNING, actor=Actor.USER)
        s.commit()

    # One decision for the two free slots: B and C outrank D, which keeps waiting.
    resumed = _undelivered(admin_factory, graph, seen)
    assert sorted(e.task_id for e in resumed if e.task_id) == sorted([b, c])
    assert _statuses(admin_factory, graph)[d] == TaskStatus.ASSIGNED


def test_simultaneous_claims_never_exceed_the_limit(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    graph = _start_graph(admin_factory, tasks=5, edges=[], max_parallel=2)
    dispatched = {e.task_id for e in _undelivered(admin_factory, graph, set())}
    with session_scope(admin_factory) as s:  # the rest as if sent before the slots filled
        workflow = s.execute(
            select(Workflow).where(Workflow.id == graph.workflow_id).with_for_update()
        ).scalar_one()
        for task in engine.list_tasks(s, workflow):
            if task.id not in dispatched:
                engine.request_dispatch(s, workflow, task)
        s.commit()
    events = _undelivered(admin_factory, graph, set())
    assert len(events) == 5
    barrier = threading.Barrier(len(events))
    results: list[runner.Claim | runner.Skipped] = []

    def deliver(event: OutboxEvent) -> None:
        barrier.wait()
        results.append(_claim(worker_factory, event))

    threads = [threading.Thread(target=deliver, args=(e,)) for e in events]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sum(isinstance(r, runner.Claim) for r in results) == 2
    assert (
        sorted(r for r in results if isinstance(r, runner.Skipped)) == [runner.Skipped.DEFERRED] * 3
    )
    statuses = list(_statuses(admin_factory, graph).values())
    assert statuses.count(TaskStatus.IN_PROGRESS) == 2


# --- seeded random graphs -----------------------------------------------------------------------


def _random_graph(rng: random.Random) -> tuple[int, list[tuple[int, int]], int, list[int]]:
    tasks = rng.randint(3, 8)
    edges = [(d, p) for d in range(tasks) for p in range(d) if rng.random() < 0.3]
    return tasks, edges, rng.randint(1, 3), [rng.randint(0, 5) for _ in range(tasks)]


@pytest.mark.parametrize("seed", range(20))
def test_random_graphs_respect_the_limit_and_dependency_order(
    admin_factory: Factory, worker_factory: Factory, seed: int
) -> None:
    """Delivers dispatches late and in random order, and finishes random subsets of the
    running attempts, checking the scheduling invariants at every claim."""
    rng = random.Random(seed)  # noqa: S311 - reproducible test data, not security
    tasks, edges, max_parallel, priorities = _random_graph(rng)
    graph = _start_graph(
        admin_factory, tasks=tasks, edges=edges, max_parallel=max_parallel, priorities=priorities
    )
    delivered: set[uuid.UUID] = set()
    running: list[runner.Claim] = []
    for _ in range(40 * tasks):
        if _workflow_status(admin_factory, graph) == WorkflowStatus.COMPLETED:
            break
        events = _undelivered(admin_factory, graph, delivered)
        rng.shuffle(events)
        late = [e for e in events if rng.random() < 0.3]  # some messages arrive later
        if len(late) == len(events) and not running:
            late = late[1:]  # but never let the whole system wait on nothing
        for event in events:
            if event in late:
                continue
            delivered.add(event.id)
            claim = _claim(worker_factory, event)
            if not isinstance(claim, runner.Claim):
                continue
            statuses = _statuses(admin_factory, graph)
            in_slots = [
                t
                for t, status in statuses.items()
                if status in {TaskStatus.QUEUED, TaskStatus.IN_PROGRESS}
            ]
            assert len(in_slots) <= max_parallel, f"seed {seed}: over the limit"
            assert all(
                statuses[p] == TaskStatus.COMPLETED for p in graph.prerequisites(claim.task_id)
            ), f"seed {seed}: started before its dependencies completed"
            running.append(claim)
        for claim in rng.sample(running, rng.randint(0, len(running))):
            running.remove(claim)
            _submit(worker_factory, claim)
            _verify(admin_factory, worker_factory, graph, claim.task_id)
    else:
        pytest.fail(f"seed {seed}: the workflow did not finish")
    assert not running
    _assert_finished_once_each(admin_factory, graph)


# --- through a real Celery worker ---------------------------------------------------------------


class _SlowAdapter(MockModelAdapter):
    """The mock provider, taking long enough per call that parallel attempts overlap."""

    def complete(
        self, request: ModelRequest, *, model: str, response_format: type[BaseModel] | None
    ) -> AdapterResult:
        time.sleep(1.0)
        return super().complete(request, model=model, response_format=response_format)


def test_a_real_worker_runs_a_diamond_with_parallel_middle_tasks(
    admin_factory: Factory,
    worker_factory: Factory,
    api_settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from celery.contrib.testing.worker import start_worker

    from app.worker import tasks
    from app.worker.celery_app import EXECUTION_QUEUE, celery_app

    # A separate Redis database for this test's broker traffic; the worker uses the test DB.
    broker_url = api_settings.model_copy(update={"celery_broker_redis_db": 13}).celery_broker_url
    monkeypatch.setattr(celery_app.conf, "broker_url", broker_url)
    # Celery caches its broker pools on first use; without this, publishing would reuse the
    # pools of an earlier test's broker URL, which this worker does not listen on.
    monkeypatch.setattr(celery_app, "_pool", None)
    monkeypatch.setattr(celery_app.amqp, "_producer_pool", None)
    monkeypatch.setattr(tasks, "get_worker_session_factory", lambda: worker_factory)
    monkeypatch.setattr(runner, "get_worker_session_factory", lambda: worker_factory)
    models = ModelGateway(api_settings, _SlowAdapter(), partial(record_model_call, worker_factory))
    monkeypatch.setattr(runner, "get_model_gateway", lambda: models)
    graph = _start_graph(
        admin_factory,
        tasks=4,
        edges=[(1, 0), (2, 0), (3, 1), (3, 2)],
        max_parallel=3,
        configuration=WRITER_CONFIG,
    )
    publish: Callable[[], object] = partial(
        relay.relay_once,
        worker_factory,
        tasks.publish_event,
        batch_size=10,
        lease_seconds=60,
        max_attempts=3,
    )

    try:
        with start_worker(
            celery_app,
            pool="threads",
            concurrency=2,
            perform_ping_check=False,
            shutdown_timeout=10,
            queues=[EXECUTION_QUEUE],
        ):
            deadline = time.monotonic() + 90
            while _workflow_status(admin_factory, graph) != WorkflowStatus.COMPLETED:
                assert time.monotonic() < deadline, "the worker did not finish the workflow"
                with session_scope(admin_factory) as s:
                    _drop_side_requests(s, graph.workflow_id)
                    s.commit()
                publish()  # dispatches, and verification requests: the worker verifies too
                time.sleep(0.2)
    finally:
        with celery_app.connection_for_write() as connection:
            connection.default_channel.queue_purge(EXECUTION_QUEUE)

    _assert_finished_once_each(admin_factory, graph)
    with session_scope(admin_factory) as s:
        runs = {
            e.task_id: e
            for e in s.execute(
                select(AgentExecution).where(AgentExecution.workflow_id == graph.workflow_id)
            ).scalars()
        }
    a, b, c, d = (runs[t] for t in graph.task_ids)
    assert a.completed_at is not None and b.completed_at is not None
    assert c.completed_at is not None
    assert a.completed_at <= min(b.started_at, c.started_at)  # sequential: A first
    assert b.started_at < c.completed_at and c.started_at < b.completed_at  # B and C overlap
    assert d.started_at >= max(b.completed_at, c.completed_at)  # D after both
