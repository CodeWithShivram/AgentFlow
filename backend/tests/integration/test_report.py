"""The execution report and timeline filters (Step 7.4, ADR-0042) against real PostgreSQL.

The finished workflow is driven through the real jobs, as workers run them: a failed attempt, an
automatic retry, deterministic verification and semantic evaluation (mock model).
"""

import uuid
from dataclasses import dataclass
from functools import partial
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.mock import MockAgent
from app.db.sync_session import session_scope
from app.execution import outbox, runner
from app.llm.gateway import ModelGateway
from app.llm.mock import MockModelAdapter
from app.models import (
    Agent,
    AgentStatus,
    AgentVersion,
    AutonomyPolicy,
    InterventionSubject,
    InterventionType,
    OutboxEvent,
    Workflow,
    WorkflowEventType,
    WorkflowStatus,
    WorkflowTask,
)
from app.services.usage import record_model_call
from app.verification import job, semantic
from app.workflows import engine
from app.workflows.event_categories import CATEGORIES
from app.workflows.state_machine import Actor
from tests.conftest import make_settings
from tests.integration.api_helpers import (
    API,
    ClientFactory,
    member,
    my_workspace,
    new_email,
    register,
)

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
_NO_MODEL = ModelGateway(
    make_settings(), MockModelAdapter(defaults={}), lambda record: uuid.uuid4()
)


@dataclass(frozen=True)
class World:
    client: TestClient
    workspace_id: uuid.UUID
    workflow_id: uuid.UUID
    task_id: uuid.UUID
    agent_id: uuid.UUID


def _world(new_client: ClientFactory, admin: Factory) -> World:
    """A RUNNING autonomous workflow with one task, pre-assigned and dispatched, plus a question
    for people that is still open."""
    client = new_client()
    assert register(client, new_email()).status_code == 201
    workspace_id = uuid.UUID(my_workspace(client))
    user_id = uuid.UUID(client.get(f"{API}/auth/me").json()["user"]["id"])
    with session_scope(admin) as s:
        agent = Agent(workspace_id=workspace_id, name="Writer R", category="writing")
        s.add(agent)
        s.flush()
        version = AgentVersion(
            agent_id=agent.id,
            version=1,
            configuration={
                "schema_version": 1,
                "system_instruction_ref": "builtin://general/v1",
                "capabilities": ["report_writing"],
                "tools": [],
            },
        )
        s.add(version)
        s.flush()
        agent.active_version_id, agent.status = version.id, AgentStatus.ACTIVE
        workflow = Workflow(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            prompt="Write the launch report",
            autonomy_policy=AutonomyPolicy.AUTONOMOUS,
        )
        s.add(workflow)
        s.flush()
        task = WorkflowTask(
            workspace_id=workspace_id,
            workflow_id=workflow.id,
            title="Draft the report",
            description="d",
            required_skills=["report_writing"],
            deliverables=["Report"],
            acceptance_criteria=[{"id": "c1", "description": "Covers the launch."}],
            assigned_agent_id=agent.id,
            assigned_agent_version_id=version.id,
        )
        s.add(task)
        s.flush()
        for target in (WorkflowStatus.PLANNING, WorkflowStatus.READY, WorkflowStatus.RUNNING):
            engine.transition_workflow(s, workflow, target, actor=Actor.USER)
        engine.open_intervention(
            s,
            workflow,
            type=InterventionType.CLARIFICATION,
            subject=InterventionSubject.ANALYSIS_QUESTION,
            requested_action="Which markets?",
            context={},
            source_key="clarification:test:a1",
        )
        s.commit()
        return World(client, workspace_id, workflow.id, task.id, agent.id)


def _latest(admin: Factory, event_type: str, task_id: uuid.UUID) -> OutboxEvent:
    with session_scope(admin) as s:
        event = s.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.event_type == event_type, OutboxEvent.task_id == task_id)
            .order_by(OutboxEvent.created_at.desc())
        ).first()
    assert event is not None
    return event


def _claim(admin: Factory, worker: Factory, world: World) -> runner.Claim:
    event = _latest(admin, outbox.TASK_DISPATCH, world.task_id)
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


def _finish(admin: Factory, worker: Factory, world: World) -> None:
    """Attempt 1 fails; the automatic retry submits; both verifiers pass; the task completes."""
    first = _claim(admin, worker, world)
    with session_scope(worker) as s:
        assert runner.finish(s, first, runner.Failure("EXECUTION_FAILED", "x")) == "rework_required"
    second = _claim(admin, worker, world)
    outcome = runner.run_agent(second, MockAgent, models=_NO_MODEL, is_current=lambda: True)
    with session_scope(worker) as s:
        assert runner.finish(s, second, outcome) == "submitted"
    verify = _latest(admin, outbox.TASK_VERIFY, world.task_id)
    with session_scope(worker) as s:
        assert (
            job.run(
                s,
                event_id=verify.id,
                workspace_id=world.workspace_id,
                workflow_id=world.workflow_id,
                task_id=world.task_id,
            )
            == "passed"
        )
    evaluate = _latest(admin, outbox.TASK_EVALUATE, world.task_id)
    with session_scope(worker) as s:
        claimed = semantic.claim(
            s,
            event_id=evaluate.id,
            workspace_id=world.workspace_id,
            workflow_id=world.workflow_id,
            task_id=world.task_id,
        )
    assert not isinstance(claimed, semantic.Skipped)
    gateway = ModelGateway(make_settings(), MockModelAdapter(), partial(record_model_call, worker))
    result = semantic.evaluate(gateway, claimed, sleep=lambda _: None)
    with session_scope(worker) as s:
        assert semantic.finish(s, claimed, result) == "passed"


def _report(client: TestClient, world: World) -> dict[str, Any]:
    response = client.get(
        f"{API}/workspaces/{world.workspace_id}/workflows/{world.workflow_id}/report"
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_the_report_of_a_finished_workflow_tells_its_whole_story(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(new_client, admin_factory)
    _finish(admin_factory, worker_factory, world)

    report = _report(world.client, world)
    assert report["report_version"] == "report/v1" and report["final"] is True
    wf = report["workflow"]
    assert (wf["prompt"], wf["status"]) == ("Write the launch report", "COMPLETED")
    assert wf["started_at"] and wf["completed_at"]

    (task,) = report["tasks"]
    assert (task["title"], task["status"], task["assigned_agent_name"]) == (
        "Draft the report",
        "COMPLETED",
        "Writer R",
    )
    assert task["assigned_agent_version"] == 1
    first, second = task["attempts"]
    assert (first["attempt_number"], first["status"], first["error_code"]) == (
        1,
        "FAILED",
        "EXECUTION_FAILED",
    )
    assert first["verification"] == [] and first["duration_seconds"] is not None
    assert second["status"] == "SUCCEEDED"
    assert sorted((v["verifier"].split("/")[0], v["outcome"]) for v in second["verification"]) == [
        ("deterministic", "PASSED"),
        ("semantic", "PASSED"),
    ]
    # The accepted attempt's deliverable, as plain text.
    (deliverable,) = task["deliverables"]
    assert deliverable == {
        "name": "Report",
        "content": "Mock content for: Report",
        "truncated": False,
    }

    recovery = report["recovery"]
    assert (recovery["retries"], recovery["failed_attempts"], recovery["escalations"]) == (1, 1, 0)
    metrics = report["metrics"]
    assert metrics["tasks_by_status"] == {"COMPLETED": 1}
    assert metrics["attempts_by_status"] == {"FAILED": 1, "SUCCEEDED": 1}
    assert (metrics["verifications_passed"], metrics["verifications_failed"]) == (2, 0)
    assert metrics["elapsed_seconds"] is not None

    # The open question was withdrawn when the workflow finished; its text is kept.
    (question,) = report["interventions"]
    assert (question["requested_action"], question["status"]) == ("Which markets?", "WITHDRAWN")
    assert question["outcome"] == "COMPLETED"

    # Usage and cost (the semantic evaluation's mock model call) reconcile.
    assert report["costs"]["calls"] == report["usage"]["totals"]["calls"] == 1
    assert report["costs"]["reconciled"] is True
    assert [r["key"] for r in report["usage"]["by_agent"]] == ["platform:semantic_evaluation"]


def test_the_report_of_a_running_workflow_is_marked_in_progress(
    new_client: ClientFactory, admin_factory: Factory
) -> None:
    world = _world(new_client, admin_factory)
    report = _report(world.client, world)
    assert report["final"] is False and report["workflow"]["status"] == "RUNNING"
    (task,) = report["tasks"]
    assert task["attempts"] == [] and task["deliverables"] == []
    assert report["metrics"]["elapsed_seconds"] >= 0
    (question,) = report["interventions"]
    assert question["status"] == "OPEN"


def test_reports_are_for_members_of_the_workspace_only(
    new_client: ClientFactory, admin_factory: Factory
) -> None:
    world = _world(new_client, admin_factory)
    viewer = member(new_client, world.client, "VIEWER")
    assert _report(viewer, world)["workflow"]["id"] == str(world.workflow_id)
    mallory = new_client()
    assert register(mallory, new_email()).status_code == 201
    url = f"{API}/workspaces/{world.workspace_id}/workflows/{world.workflow_id}/report"
    assert mallory.get(url).status_code == 404
    own = f"{API}/workspaces/{my_workspace(mallory)}/workflows/{world.workflow_id}/report"
    assert mallory.get(own).status_code == 404


def test_the_timeline_filters_by_task_and_category(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(new_client, admin_factory)
    _finish(admin_factory, worker_factory, world)
    url = f"{API}/workspaces/{world.workspace_id}/workflows/{world.workflow_id}/events"

    every = world.client.get(url, params={"limit": 500}).json()["items"]
    for_task = world.client.get(url, params={"limit": 500, "task_id": str(world.task_id)}).json()
    assert for_task["items"] and all(e["task_id"] == str(world.task_id) for e in for_task["items"])
    assert len(for_task["items"]) < len(every)
    people = world.client.get(url, params={"limit": 500, "category": "people"}).json()["items"]
    assert {e["event_type"] for e in people} == {"INTERVENTION_OPENED", "INTERVENTION_RESOLVED"}
    planning = world.client.get(url, params={"category": "planning"}).json()["items"]
    assert planning and all(
        CATEGORIES[WorkflowEventType(e["event_type"])] == "planning" for e in planning
    )
    assert world.client.get(url, params={"category": "gossip"}).status_code == 422
