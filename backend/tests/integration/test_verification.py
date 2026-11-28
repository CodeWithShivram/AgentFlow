"""Platform verification of submitted tasks against real PostgreSQL: deterministic checks
(Step 5.1, ADR-0030) and semantic evaluation (Step 5.2, ADR-0031), with the mock model provider.

Worker-side code runs as the restricted worker role (`worker_factory`), as Celery workers do.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

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
    AgentExecution,
    AgentSelection,
    AgentStatus,
    AgentVersion,
    AutonomyPolicy,
    InboxMessage,
    LlmUsage,
    OutboxEvent,
    OutboxStatus,
    PauseReason,
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
from app.schemas.agents import AgentConfiguration
from app.services.usage import record_model_call
from app.verification import job, semantic
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
    agent_ids: tuple[uuid.UUID, ...]


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


def _world(
    admin: Factory,
    *,
    attempt_count: int = 0,
    skills: tuple[str, ...] = ("report_writing",),
    agents: int = 1,
) -> World:
    """A RUNNING workflow: task A (pre-assigned to the first of `agents` workspace agents, with a
    deliverable and a criterion) is dispatched; task B (pre-assigned) waits for A."""
    with session_scope(admin) as s:
        user = User(email=f"{uuid.uuid4().hex[:8]}@example.com", password_hash="h")
        s.add(user)
        s.flush()
        workspace = Workspace(owner_id=user.id, name="W")
        s.add(workspace)
        s.flush()
        created = []
        for name in ("Writer X", "Writer Y", "Writer Z")[:agents]:
            agent = Agent(workspace_id=workspace.id, name=name, category="writing")
            s.add(agent)
            s.flush()
            # A runtime reference, so the selection rules can find these agents eligible.
            configuration = {
                **CONFIG,
                "system_instruction_ref": "builtin://general/v1",
                "capabilities": list(skills),
            }
            version = AgentVersion(agent_id=agent.id, version=1, configuration=configuration)
            s.add(version)
            s.flush()
            agent.active_version_id, agent.status = version.id, AgentStatus.ACTIVE
            created.append((agent, version))
        agent, version = created[0]
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
                required_skills=list(skills),
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
        return World(
            workspace.id, workflow.id, tasks[0].id, tasks[1].id, tuple(a.id for a, _ in created)
        )


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


def _evaluate(
    admin: Factory, worker: Factory, world: World, adapter: MockModelAdapter | None = None
) -> str:
    """Runs the semantic evaluation job on the task's latest evaluation request, as a worker
    would: claim, model call outside any transaction (mock provider), finish."""
    event = _events(admin, outbox.TASK_EVALUATE, world.task_id)[-1]
    with session_scope(worker) as s:
        claimed = semantic.claim(
            s,
            event_id=event.id,
            workspace_id=world.workspace_id,
            workflow_id=world.workflow_id,
            task_id=world.task_id,
        )
    if isinstance(claimed, semantic.Skipped):
        return claimed.value
    gateway = ModelGateway(
        make_settings(), adapter or MockModelAdapter(), partial(record_model_call, worker)
    )
    outcome = semantic.evaluate(gateway, claimed, sleep=lambda _: None)
    with session_scope(worker) as s:
        return semantic.finish(s, claimed, outcome)


def _evaluation(**changes: Any) -> str:
    values: dict[str, Any] = {
        "objective": "met",
        "objective_reason": "Done.",
        "criteria": [
            {"criterion_id": "c1", "verdict": "met", "evidence": "Report.", "reason": "Covered."}
        ],
        "complete": True,
        "consistent": True,
        "assumptions_explicit": True,
        "conflicts_with_requirements": False,
        "summary": "Fine.",
        **changes,
    }
    return semantic.Evaluation.model_validate(values).model_dump_json()


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


def _escalation(admin: Factory, task_id: uuid.UUID) -> WorkflowEvent:
    """The task's escalation event; the platform then opens a request for people (ADR-0035)."""
    events = _task_events(admin, task_id)
    *_, escalated, opened = events
    assert escalated.payload["to"] == "ESCALATED"
    assert opened.event_type == WorkflowEventType.INTERVENTION_OPENED
    assert opened.payload["subject"] == "task_escalation"
    return escalated


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


def test_a_passing_submission_is_evaluated_then_completes_and_starts_its_dependents(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    claim = _submit(admin_factory, worker_factory, world)
    (event,) = _events(admin_factory, outbox.TASK_VERIFY, world.task_id)

    assert _verify(worker_factory, world, event) == "passed"

    # Step 5.2 (ADR-0031): passing the checks hands the task to the semantic evaluation.
    assert _status(admin_factory, world.task_id) == TaskStatus.VERIFYING
    (evaluate,) = _events(admin_factory, outbox.TASK_EVALUATE, world.task_id)
    assert evaluate.payload == {
        "workspace_id": str(world.workspace_id),
        "workflow_id": str(world.workflow_id),
        "task_id": str(world.task_id),
    }
    (checks,) = _results(admin_factory)
    assert (checks.execution_id, checks.verifier) == (claim.execution_id, "deterministic/v4")
    assert checks.outcome == VerificationOutcome.PASSED and checks.issues == []
    assert checks.findings is None
    assert {c["name"]: c["outcome"] for c in checks.checks}["deliverables"] == "passed"
    assert _status(admin_factory, world.dependent_id) == TaskStatus.PENDING

    assert _evaluate(admin_factory, worker_factory, world) == "passed"

    assert _status(admin_factory, world.task_id) == TaskStatus.COMPLETED
    evaluation = next(r for r in _results(admin_factory) if r.verifier == "semantic/v1")
    assert evaluation.execution_id == claim.execution_id
    assert evaluation.outcome == VerificationOutcome.PASSED
    assert evaluation.findings is not None and evaluation.findings["objective"] == "met"
    started, completed = [
        e for e in _task_events(admin_factory, world.task_id) if e.payload.get("verifier")
    ]
    assert started.event_type == WorkflowEventType.VERIFICATION_STARTED
    assert completed.event_type == WorkflowEventType.TASK_COMPLETED
    assert completed.payload["actor"] == "VERIFIER"
    assert completed.payload["verifier"] == "semantic/v1"
    assert completed.payload["verification_id"] == str(evaluation.id)
    with session_scope(admin_factory) as s:  # the evaluator's call belongs to the attempt
        usage = s.execute(select(LlmUsage)).scalar_one()
    assert (usage.execution_id, usage.purpose) == (claim.execution_id, "semantic_evaluation")
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

    # Step 5.3 (ADR-0032): the scheduler retries at once; see the rework tests below.
    assert _status(admin_factory, world.task_id) == TaskStatus.ASSIGNED
    (result,) = _results(admin_factory)
    assert result.outcome == VerificationOutcome.FAILED
    assert result.issues == [
        {"code": "CRITERION_NOT_MET", "severity": "error", "criterion_id": "c1"}
    ]
    failed = _task_events(admin_factory, world.task_id)[-2]
    assert failed.event_type == WorkflowEventType.VERIFICATION_FAILED
    assert failed.payload["reason"] == "VERIFICATION_FAILED"
    assert failed.payload["issue_codes"] == ["CRITERION_NOT_MET"]
    assert _status(admin_factory, world.dependent_id) == TaskStatus.PENDING


def test_a_failing_last_attempt_escalates_the_task_and_blocks_the_workflow(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory, attempt_count=runner.MAX_ATTEMPTS_PER_TASK - 1)
    _submit(admin_factory, worker_factory, world, _HonestAgent)
    (event,) = _events(admin_factory, outbox.TASK_VERIFY, world.task_id)

    # Step 5.5 (ADR-0034): exhausted attempts escalate to people instead of failing.
    assert _verify(worker_factory, world, event) == "escalated"

    assert _status(admin_factory, world.task_id) == TaskStatus.ESCALATED
    with session_scope(admin_factory) as s:
        status = s.execute(
            select(Workflow.status).where(Workflow.id == world.workflow_id)
        ).scalar_one()
    assert status == WorkflowStatus.BLOCKED


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


# --- semantic evaluation (Step 5.2, ADR-0031) ---------------------------------------------------


def _evaluating(admin: Factory, worker: Factory, world: World) -> None:
    _submit(admin, worker, world)
    (event,) = _events(admin, outbox.TASK_VERIFY, world.task_id)
    assert _verify(worker, world, event) == "passed"


def test_an_evaluation_judging_a_criterion_not_met_sends_the_task_back(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    _evaluating(admin_factory, worker_factory, world)
    adapter = MockModelAdapter()
    not_met = {"criterion_id": "c1", "verdict": "not_met", "evidence": "None.", "reason": "Absent."}
    adapter.script("semantic_evaluation", _evaluation(criteria=[not_met]))

    assert _evaluate(admin_factory, worker_factory, world, adapter) == "rework_required"

    assert _status(admin_factory, world.task_id) == TaskStatus.ASSIGNED  # retried (ADR-0032)
    evaluation = next(r for r in _results(admin_factory) if r.verifier == "semantic/v1")
    assert evaluation.outcome == VerificationOutcome.FAILED
    assert evaluation.issues == [
        {"code": "CRITERION_JUDGED_NOT_MET", "severity": "error", "criterion_id": "c1"}
    ]
    assert evaluation.findings is not None
    assert evaluation.findings["criteria"][0]["reason"] == "Absent."  # stored as findings only
    failed = _task_events(admin_factory, world.task_id)[-2]
    assert failed.event_type == WorkflowEventType.VERIFICATION_FAILED
    assert failed.payload["issue_codes"] == ["CRITERION_JUDGED_NOT_MET"]


def test_warnings_alone_do_not_fail_the_evaluation(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    _evaluating(admin_factory, worker_factory, world)
    adapter = MockModelAdapter()
    adapter.script("semantic_evaluation", _evaluation(objective="partially_met", complete=False))

    assert _evaluate(admin_factory, worker_factory, world, adapter) == "passed"

    evaluation = next(r for r in _results(admin_factory) if r.verifier == "semantic/v1")
    assert [i["code"] for i in evaluation.issues] == ["OBJECTIVE_PARTIAL", "INCOMPLETE"]
    assert _status(admin_factory, world.task_id) == TaskStatus.COMPLETED


def test_an_evaluation_that_cannot_complete_escalates_without_costing_an_attempt(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    _evaluating(admin_factory, worker_factory, world)
    adapter = MockModelAdapter()
    adapter.script("semantic_evaluation", "not json", "still not json", "nor this")

    assert _evaluate(admin_factory, worker_factory, world, adapter) == "escalated"

    assert _status(admin_factory, world.task_id) == TaskStatus.ESCALATED
    assert [r.verifier for r in _results(admin_factory)] == ["deterministic/v4"]
    escalated = _escalation(admin_factory, world.task_id)
    assert escalated.payload["reason"] == "EVALUATION_UNAVAILABLE"
    assert escalated.payload["error_code"] == "MODEL_OUTPUT_INVALID"
    with session_scope(admin_factory) as s:
        task = s.get(WorkflowTask, world.task_id)
        status = s.execute(
            select(Workflow.status).where(Workflow.id == world.workflow_id)
        ).scalar_one()
        assert task is not None and task.attempt_count == 0
    assert status == WorkflowStatus.BLOCKED  # a required task needs people (ADR-0009)


def test_duplicate_and_stale_evaluation_requests_change_nothing(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    _evaluating(admin_factory, worker_factory, world)
    (event,) = _events(admin_factory, outbox.TASK_EVALUATE, world.task_id)
    assert _evaluate(admin_factory, worker_factory, world) == "passed"

    with session_scope(worker_factory) as s:
        duplicate = semantic.claim(
            s,
            event_id=event.id,
            workspace_id=world.workspace_id,
            workflow_id=world.workflow_id,
            task_id=world.task_id,
        )
    assert duplicate == semantic.Skipped.DUPLICATE
    with session_scope(admin_factory) as s:  # a late second request for an evaluated task
        workflow = s.get(Workflow, world.workflow_id)
        task = s.get(WorkflowTask, world.task_id)
        assert workflow is not None and task is not None
        engine.request_evaluation(s, workflow, task)
        s.commit()
    assert _evaluate(admin_factory, worker_factory, world) == semantic.Skipped.STALE
    assert len(_results(admin_factory)) == 2


def test_an_evaluation_finished_after_the_task_was_cancelled_changes_nothing(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    _evaluating(admin_factory, worker_factory, world)
    event = _events(admin_factory, outbox.TASK_EVALUATE, world.task_id)[-1]
    with session_scope(worker_factory) as s:
        claimed = semantic.claim(
            s,
            event_id=event.id,
            workspace_id=world.workspace_id,
            workflow_id=world.workflow_id,
            task_id=world.task_id,
        )
    assert isinstance(claimed, semantic.EvaluationJob)
    with session_scope(admin_factory) as s:  # cancelled while the model was working
        workflow = s.execute(
            select(Workflow).where(Workflow.id == world.workflow_id).with_for_update()
        ).scalar_one()
        task = s.get(WorkflowTask, world.task_id)
        assert task is not None
        engine.transition_task(s, workflow, task, TaskStatus.CANCELLED, actor=Actor.USER)
        s.commit()
    gateway = ModelGateway(make_settings(), MockModelAdapter(), lambda record: uuid.uuid4())
    outcome = semantic.evaluate(gateway, claimed, sleep=lambda _: None)
    with session_scope(worker_factory) as s:
        assert semantic.finish(s, claimed, outcome) == semantic.Skipped.STALE
    assert _status(admin_factory, world.task_id) == TaskStatus.CANCELLED
    assert [r.verifier for r in _results(admin_factory)] == ["deterministic/v4"]


def test_recovery_resends_lost_evaluations_and_escalates_interrupted_ones(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    lost_world = _world(admin_factory)
    _evaluating(admin_factory, worker_factory, lost_world)
    (lost,) = _events(admin_factory, outbox.TASK_EVALUATE, lost_world.task_id)
    with session_scope(admin_factory) as s:
        s.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == lost.id)
            .values(
                status=OutboxStatus.PUBLISHED,
                published_at=datetime.now(UTC) - recovery.LOST_DISPATCH_AFTER * 2,
            )
        )
        s.commit()

    interrupted_world = _world(admin_factory)
    _evaluating(admin_factory, worker_factory, interrupted_world)
    (claimed_event,) = _events(admin_factory, outbox.TASK_EVALUATE, interrupted_world.task_id)
    with session_scope(worker_factory) as s:  # claimed, then the worker was lost
        assert isinstance(
            semantic.claim(
                s,
                event_id=claimed_event.id,
                workspace_id=interrupted_world.workspace_id,
                workflow_id=interrupted_world.workflow_id,
                task_id=interrupted_world.task_id,
            ),
            semantic.EvaluationJob,
        )
    # The lost request is resent; the claimed one may still be running.
    assert recovery.recover_once(worker_factory) == recovery.RecoveryReport(reevaluated=1)
    with session_scope(admin_factory) as s:
        s.execute(
            update(InboxMessage)
            .where(InboxMessage.event_id == claimed_event.id)
            .values(
                processed_at=datetime.now(UTC)
                - recovery.INTERRUPTED_EVALUATION_AFTER
                - timedelta(minutes=1)
            )
        )
        s.commit()

    report = recovery.recover_once(worker_factory)

    assert report == recovery.RecoveryReport(interrupted_evaluations=1)
    assert len(_events(admin_factory, outbox.TASK_EVALUATE, lost_world.task_id)) == 2
    assert _evaluate(admin_factory, worker_factory, lost_world) == "passed"
    assert _status(admin_factory, interrupted_world.task_id) == TaskStatus.ESCALATED
    escalated = _escalation(admin_factory, interrupted_world.task_id)
    assert escalated.payload["reason"] == "EVALUATION_UNAVAILABLE"
    assert escalated.payload["error_code"] == "EVALUATION_INTERRUPTED"


# --- rework instructions, bounded retries and reassignment (Step 5.3, ADR-0032) -----------------

# No built-in agent has both skills, so only the test's own agents are eligible.
RARE_SKILLS = ("report_writing", "code_generation")


def _fail_verification(admin: Factory, worker: Factory, world: World) -> runner.Claim:
    """One attempt whose agent says it did not meet the criterion, verified by the platform."""
    claim = _submit(admin, worker, world, _HonestAgent)
    event = _events(admin, outbox.TASK_VERIFY, world.task_id)[-1]
    assert _verify(worker, world, event) == "rework_required"
    return claim


def _claim_next(admin: Factory, worker: Factory, world: World) -> runner.Claim:
    event = _events(admin, outbox.TASK_DISPATCH, world.task_id)[-1]
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
    return claim


def test_a_failed_verification_is_retried_with_corrective_instructions(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    _fail_verification(admin_factory, worker_factory, world)

    retried = _task_events(admin_factory, world.task_id)[-1]
    assert retried.event_type == WorkflowEventType.TASK_ASSIGNED
    assert retried.payload["actor"] == "SCHEDULER" and retried.payload["automatic_retry"] is True
    assert retried.payload["policy"] == "retry/v1"
    assert len(_events(admin_factory, outbox.TASK_DISPATCH, world.task_id)) == 2

    claim = _claim_next(admin_factory, worker_factory, world)

    expected = ["Meet acceptance criterion c1: your previous attempt reported it as not met."]
    assert claim.attempt_number == 2 and claim.rework_instructions == expected
    configuration = AgentConfiguration.model_validate(CONFIG)
    contract = runner.build_contract(claim, configuration)
    assert contract.contract_version == 6 and contract.rework_instructions == expected
    with session_scope(admin_factory) as s:
        stored = s.execute(
            select(AgentExecution.attempt_number, AgentExecution.rework_instructions).order_by(
                AgentExecution.attempt_number
            )
        ).all()
    assert [tuple(row) for row in stored] == [(1, []), (2, expected)]


def test_the_third_attempt_goes_to_another_eligible_agent_once(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory, skills=RARE_SKILLS, agents=2)
    first, second = world.agent_ids
    _fail_verification(admin_factory, worker_factory, world)  # attempt 1, same agent retries
    assert _task_events(admin_factory, world.task_id)[-1].event_type == (
        WorkflowEventType.TASK_ASSIGNED
    )
    _fail_verification(admin_factory, worker_factory, world)  # attempt 2: reassigned

    reassigned = _task_events(admin_factory, world.task_id)[-1]
    assert reassigned.event_type == WorkflowEventType.TASK_REASSIGNED
    assert reassigned.payload["actor"] == "SCHEDULER"
    assert reassigned.payload["previous_agent_id"] == str(first)
    assert reassigned.payload["agent_id"] == str(second)
    with session_scope(admin_factory) as s:
        selection = s.execute(select(AgentSelection)).scalar_one()
        task = s.get(WorkflowTask, world.task_id)
        assert task is not None
    assert selection.agent_id == second and selection.decided_by_user_id is None  # platform
    assert selection.eligible and selection.rank is not None
    assert str(selection.id) == reassigned.payload["selection_id"]
    assert (task.assigned_agent_id, task.attempt_count) == (second, 2)

    claim = _claim_next(admin_factory, worker_factory, world)
    assert claim.agent_id == second and claim.attempt_number == 3
    assert claim.rework_instructions == [
        "Meet acceptance criterion c1: your previous attempt reported it as not met."
    ]


def test_with_no_other_eligible_agent_the_same_agent_retries(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory, skills=RARE_SKILLS)
    for _ in range(2):
        _fail_verification(admin_factory, worker_factory, world)

    last = _task_events(admin_factory, world.task_id)[-1]
    assert last.event_type == WorkflowEventType.TASK_ASSIGNED
    assert last.payload["agent_id"] == str(world.agent_ids[0])
    with session_scope(admin_factory) as s:
        assert s.execute(select(AgentSelection)).first() is None
    assert _claim_next(admin_factory, worker_factory, world).attempt_number == 3


@pytest.mark.parametrize("code", ["TOOL_PERMISSION_DENIED", "AGENT_RUNTIME_UNAVAILABLE"])
def test_policy_failures_are_escalated_to_people(
    admin_factory: Factory, worker_factory: Factory, code: str
) -> None:
    world = _world(admin_factory)
    claim = _claim_next(admin_factory, worker_factory, world)
    with session_scope(worker_factory) as s:
        assert runner.finish(s, claim, runner.Failure(code, "Refused.")) == "rework_required"

    # Step 5.5 (ADR-0034): not retried, and escalated instead of waiting in rework.
    assert _status(admin_factory, world.task_id) == TaskStatus.ESCALATED
    escalated = _escalation(admin_factory, world.task_id)
    assert escalated.payload["reason"] == "POLICY_FAILURE"
    assert escalated.payload["failure_codes"] == [code]
    assert len(_events(admin_factory, outbox.TASK_DISPATCH, world.task_id)) == 1


# --- escalation and platform acceptance (Step 5.5, ADR-0034) -----------------------------------


def test_only_platform_verification_can_complete_a_task(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    _evaluating(admin_factory, worker_factory, world)  # checks passed, evaluation pending

    # The engine refuses, even for the VERIFIER actor, while the evaluation has not passed.
    with session_scope(admin_factory) as s:
        workflow = s.execute(
            select(Workflow).where(Workflow.id == world.workflow_id).with_for_update()
        ).scalar_one()
        task = s.get(WorkflowTask, world.task_id)
        assert task is not None
        with pytest.raises(engine.TransitionPreconditionError):
            engine.transition_task(s, workflow, task, TaskStatus.COMPLETED, actor=Actor.VERIFIER)
    # The database refuses any other write too, whatever the role.
    with (
        pytest.raises(DBAPIError, match="only platform verification"),
        session_scope(admin_factory) as s,
    ):
        s.execute(
            update(WorkflowTask)
            .where(WorkflowTask.id == world.task_id)
            .values(status=TaskStatus.COMPLETED)
        )
    assert _status(admin_factory, world.task_id) == TaskStatus.VERIFYING

    assert _evaluate(admin_factory, worker_factory, world) == "passed"  # the platform's way
    assert _status(admin_factory, world.task_id) == TaskStatus.COMPLETED


def test_a_workflow_is_paused_once_when_its_deadline_passes(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    assert recovery.recover_once(worker_factory) == recovery.RecoveryReport()
    with session_scope(admin_factory) as s:
        s.execute(
            update(Workflow)
            .where(Workflow.id == world.workflow_id)
            .values(deadline=datetime.now(UTC) - timedelta(minutes=1))
        )
        s.commit()

    assert recovery.recover_once(worker_factory) == recovery.RecoveryReport(paused_at_deadline=1)

    with session_scope(admin_factory) as s:
        workflow = s.execute(
            select(Workflow).where(Workflow.id == world.workflow_id).with_for_update()
        ).scalar_one()
        assert workflow.status == WorkflowStatus.PAUSED
        paused = s.execute(
            select(WorkflowEvent)
            .where(WorkflowEvent.workflow_id == world.workflow_id, WorkflowEvent.task_id.is_(None))
            .order_by(WorkflowEvent.sequence.desc())
            .limit(1)
        ).scalar_one()
        assert paused.payload["reason"] == "DEADLINE_PASSED" and paused.payload["actor"] == "SYSTEM"
        # Step 6.2 (ADR-0036): the workflow says why it is paused.
        assert workflow.pause_reason == PauseReason.DEADLINE_PASSED
        assert workflow.paused_at is not None and workflow.paused_by_user_id is None
        # People decided to carry on: the deadline does not pause it again.
        engine.transition_workflow(s, workflow, WorkflowStatus.RUNNING, actor=Actor.USER)
        s.commit()
    assert recovery.recover_once(worker_factory) == recovery.RecoveryReport()
