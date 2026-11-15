"""Platform verification of submitted tasks against real PostgreSQL (Step 5.1, ADR-0030).

Worker-side code runs as the restricted worker role (`worker_factory`), as Celery workers do.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import AgentContext, BaseAgent
from app.agents.contract import AgentSubmission, CriterionStatus, TaskContract
from app.agents.mock import MockAgent
from app.db.sync_session import session_scope
from app.execution import outbox, recovery, runner
from app.llm.gateway import ModelGateway
from app.llm.mock import MockModelAdapter
from app.models import (
    Agent,
    AgentStatus,
    AgentVersion,
    AutonomyPolicy,
    OutboxEvent,
    OutboxStatus,
    TaskDependency,
    TaskStatus,
    User,
    VerificationOutcome,
    VerificationResult,
    Workflow,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowStatus,
    WorkflowTask,
    Workspace,
)
from app.verification import job
from app.workflows import engine
from app.workflows.state_machine import Actor
from tests.conftest import make_settings

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
_NO_MODEL = ModelGateway(
    make_settings(), MockModelAdapter(defaults={}), lambda record: uuid.uuid4()
)
CONFIG = {
    "schema_version": 1,
    "system_instruction_ref": "workspace://prompts/w/v1",
    "capabilities": ["report_writing"],
    "tools": [],
}
CRITERIA = [{"id": "c1", "description": "Covers the topic."}]


@dataclass(frozen=True)
class World:
    workspace_id: uuid.UUID
    workflow_id: uuid.UUID
    task_id: uuid.UUID
    dependent_id: uuid.UUID


class _HonestAgent(MockAgent):
    """Says it did not meet the acceptance criteria."""

    def execute(self, contract: TaskContract, context: AgentContext) -> AgentSubmission:
        submission = super().execute(contract, context)
        criteria = [
            c.model_copy(update={"status": CriterionStatus.NOT_MET}) for c in submission.criteria
        ]
        return submission.model_copy(update={"criteria": criteria})


def _side_requests(s: Session) -> None:
    s.execute(
        delete(OutboxEvent).where(
            OutboxEvent.event_type.in_([outbox.WORKFLOW_ANALYZE, outbox.WORKFLOW_ORCHESTRATE])
        )
    )


def _world(admin: Factory, *, attempt_count: int = 0) -> World:
    """A RUNNING workflow: task A (pre-assigned, with a deliverable and a criterion) is dispatched;
    task B (pre-assigned) waits for A."""
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
        version = AgentVersion(agent_id=agent.id, version=1, configuration=CONFIG)
        s.add(version)
        s.flush()
        agent.active_version_id, agent.status = version.id, AgentStatus.ACTIVE
        workflow = Workflow(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            prompt="p",
            autonomy_policy=AutonomyPolicy.AUTONOMOUS,
        )
        s.add(workflow)
        s.flush()
        tasks = [
            WorkflowTask(
                workspace_id=workspace.id,
                workflow_id=workflow.id,
                title=title,
                description="d",
                required_skills=["report_writing"],
                deliverables=["Report"],
                acceptance_criteria=CRITERIA,
                attempt_count=attempt_count,
                assigned_agent_id=agent.id,
                assigned_agent_version_id=version.id,
            )
            for title in ("A", "B")
        ]
        s.add_all(tasks)
        s.flush()
        s.add(
            TaskDependency(
                task_id=tasks[1].id, depends_on_task_id=tasks[0].id, workflow_id=workflow.id
            )
        )
        s.flush()
        for target in (WorkflowStatus.PLANNING, WorkflowStatus.READY, WorkflowStatus.RUNNING):
            engine.transition_workflow(s, workflow, target, actor=Actor.USER)
        _side_requests(s)
        s.commit()
        return World(workspace.id, workflow.id, tasks[0].id, tasks[1].id)


def _events(admin: Factory, event_type: str, task_id: uuid.UUID) -> list[OutboxEvent]:
    with session_scope(admin) as s:
        return list(
            s.execute(
                select(OutboxEvent)
                .where(OutboxEvent.event_type == event_type, OutboxEvent.task_id == task_id)
                .order_by(OutboxEvent.created_at)
            ).scalars()
        )


def _submit(
    admin: Factory, worker: Factory, world: World, agent: type[BaseAgent] = MockAgent
) -> runner.Claim:
    (event,) = _events(admin, outbox.TASK_DISPATCH, world.task_id)[-1:]
    with session_scope(worker) as s:
        claim = runner.claim(
            s,
            event_id=event.id,
            workspace_id=world.workspace_id,
            workflow_id=world.workflow_id,
            task_id=world.task_id,
            agent_version_id=uuid.UUID(event.payload["agent_version_id"]),
        )
    assert isinstance(claim, runner.Claim)
    outcome = runner.run_agent(claim, agent, models=_NO_MODEL, is_current=lambda: True)
    with session_scope(worker) as s:
        assert runner.finish(s, claim, outcome) == "submitted"
    return claim


def _verify(worker: Factory, world: World, event: OutboxEvent) -> str:
    with session_scope(worker) as s:
        return job.run(
            s,
            event_id=event.id,
            workspace_id=world.workspace_id,
            workflow_id=world.workflow_id,
            task_id=world.task_id,
        )


def _status(admin: Factory, task_id: uuid.UUID) -> TaskStatus:
    with session_scope(admin) as s:
        return s.execute(select(WorkflowTask.status).where(WorkflowTask.id == task_id)).scalar_one()


def _results(admin: Factory) -> list[VerificationResult]:
    with session_scope(admin) as s:
        return list(s.execute(select(VerificationResult)).scalars())


def _task_events(admin: Factory, task_id: uuid.UUID) -> list[WorkflowEvent]:
    with session_scope(admin) as s:
        return list(
            s.execute(
                select(WorkflowEvent)
                .where(WorkflowEvent.task_id == task_id)
                .order_by(WorkflowEvent.sequence)
            ).scalars()
        )


def test_a_submission_requests_verification_in_the_same_transaction(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    assert _events(admin_factory, outbox.TASK_VERIFY, world.task_id) == []
    _submit(admin_factory, worker_factory, world)

    (event,) = _events(admin_factory, outbox.TASK_VERIFY, world.task_id)
    assert event.payload == {  # identifiers only
        "workspace_id": str(world.workspace_id),
        "workflow_id": str(world.workflow_id),
        "task_id": str(world.task_id),
    }
    assert _status(admin_factory, world.task_id) == TaskStatus.SUBMITTED


def test_a_passing_submission_completes_the_task_and_starts_its_dependents(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    claim = _submit(admin_factory, worker_factory, world)
    (event,) = _events(admin_factory, outbox.TASK_VERIFY, world.task_id)

    assert _verify(worker_factory, world, event) == "passed"

    assert _status(admin_factory, world.task_id) == TaskStatus.COMPLETED
    (result,) = _results(admin_factory)
    assert (result.execution_id, result.verifier) == (claim.execution_id, "deterministic/v1")
    assert result.outcome == VerificationOutcome.PASSED and result.issues == []
    assert {c["name"]: c["outcome"] for c in result.checks}["deliverables"] == "passed"
    started, completed = [
        e for e in _task_events(admin_factory, world.task_id) if e.payload.get("verifier")
    ]
    assert started.event_type == WorkflowEventType.VERIFICATION_STARTED
    assert completed.event_type == WorkflowEventType.TASK_COMPLETED
    assert completed.payload["actor"] == "VERIFIER"
    assert completed.payload["verification_id"] == str(result.id)
    # Only now does the dependent task start, through the scheduler (ADR-0028).
    assert _status(admin_factory, world.dependent_id) == TaskStatus.ASSIGNED
    assert len(_events(admin_factory, outbox.TASK_DISPATCH, world.dependent_id)) == 1


def test_a_failing_submission_goes_back_for_rework_with_its_issues(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    _submit(admin_factory, worker_factory, world, _HonestAgent)
    (event,) = _events(admin_factory, outbox.TASK_VERIFY, world.task_id)

    assert _verify(worker_factory, world, event) == "rework_required"

    assert _status(admin_factory, world.task_id) == TaskStatus.REWORK_REQUIRED
    (result,) = _results(admin_factory)
    assert result.outcome == VerificationOutcome.FAILED
    assert result.issues == [
        {"code": "CRITERION_NOT_MET", "severity": "error", "criterion_id": "c1"}
    ]
    failed = _task_events(admin_factory, world.task_id)[-1]
    assert failed.event_type == WorkflowEventType.VERIFICATION_FAILED
    assert failed.payload["reason"] == "VERIFICATION_FAILED"
    assert failed.payload["issue_codes"] == ["CRITERION_NOT_MET"]
    assert _status(admin_factory, world.dependent_id) == TaskStatus.PENDING


def test_a_failing_last_attempt_fails_the_task_and_the_workflow(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory, attempt_count=runner.MAX_ATTEMPTS_PER_TASK - 1)
    _submit(admin_factory, worker_factory, world, _HonestAgent)
    (event,) = _events(admin_factory, outbox.TASK_VERIFY, world.task_id)

    assert _verify(worker_factory, world, event) == "failed"

    assert _status(admin_factory, world.task_id) == TaskStatus.FAILED
    with session_scope(admin_factory) as s:
        status = s.execute(
            select(Workflow.status).where(Workflow.id == world.workflow_id)
        ).scalar_one()
    assert status == WorkflowStatus.FAILED


def test_duplicate_and_stale_verification_requests_change_nothing(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    _submit(admin_factory, worker_factory, world)
    (event,) = _events(admin_factory, outbox.TASK_VERIFY, world.task_id)
    assert _verify(worker_factory, world, event) == "passed"

    assert _verify(worker_factory, world, event) == job.Skipped.DUPLICATE
    with session_scope(admin_factory) as s:  # a second request for a task already verified
        workflow = s.get(Workflow, world.workflow_id)
        task = s.get(WorkflowTask, world.task_id)
        assert workflow is not None and task is not None
        engine.request_verification(s, workflow, task)
        s.commit()
    late = _events(admin_factory, outbox.TASK_VERIFY, world.task_id)[-1]
    assert _verify(worker_factory, world, late) == job.Skipped.STALE
    assert len(_results(admin_factory)) == 1


def test_a_task_cancelled_before_verification_is_left_alone(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    _submit(admin_factory, worker_factory, world)
    (event,) = _events(admin_factory, outbox.TASK_VERIFY, world.task_id)
    with session_scope(admin_factory) as s:
        workflow = s.execute(
            select(Workflow).where(Workflow.id == world.workflow_id).with_for_update()
        ).scalar_one()
        task = s.get(WorkflowTask, world.task_id)
        assert task is not None
        engine.transition_task(s, workflow, task, TaskStatus.CANCELLED, actor=Actor.USER)
        s.commit()

    assert _verify(worker_factory, world, event) == job.Skipped.STALE
    assert _status(admin_factory, world.task_id) == TaskStatus.CANCELLED
    assert _results(admin_factory) == []


def test_verification_results_are_append_only(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    _submit(admin_factory, worker_factory, world)
    (event,) = _events(admin_factory, outbox.TASK_VERIFY, world.task_id)
    _verify(worker_factory, world, event)
    statement = update(VerificationResult).values(outcome=VerificationOutcome.FAILED)

    with pytest.raises(ProgrammingError), session_scope(worker_factory) as s:  # no UPDATE grant
        s.execute(statement)
    with pytest.raises(DBAPIError, match="append-only"), session_scope(admin_factory) as s:
        s.execute(statement)


def test_a_lost_verification_request_is_sent_again(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    _submit(admin_factory, worker_factory, world)
    (lost,) = _events(admin_factory, outbox.TASK_VERIFY, world.task_id)

    def published(ago: timedelta) -> None:
        with session_scope(admin_factory) as s:
            s.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == lost.id)
                .values(status=OutboxStatus.PUBLISHED, published_at=datetime.now(UTC) - ago)
            )
            s.commit()

    published(timedelta(minutes=1))
    assert recovery.recover_once(worker_factory) == recovery.RecoveryReport()  # may be queued
    published(recovery.LOST_DISPATCH_AFTER + timedelta(minutes=1))

    assert recovery.recover_once(worker_factory) == recovery.RecoveryReport(reverified=1)

    resent = _events(admin_factory, outbox.TASK_VERIFY, world.task_id)[-1]
    assert resent.id != lost.id
    assert _verify(worker_factory, world, resent) == "passed"
    assert _verify(worker_factory, world, lost) == job.Skipped.STALE  # the late original


def test_the_worker_role_can_record_but_not_change_results(worker_factory: Factory) -> None:
    with session_scope(worker_factory) as s:
        privileges = set(
            s.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = 'verification_results' AND grantee = current_user"
                )
            ).scalars()
        )
    assert privileges == {"SELECT", "INSERT"}
