"""Requests that need a person (Step 6.1, ADR-0035) against real PostgreSQL: analysis questions,
plan approvals, agents' approval requests and escalations, through the API and the workers.

Worker-side code runs as the restricted worker role (`worker_factory`), as Celery workers do.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import AgentContext
from app.agents.contract import AgentSubmission, ApprovalRequest, TaskContract
from app.agents.mock import MockAgent
from app.core.config import Settings
from app.db.sync_session import session_scope
from app.execution import outbox, recovery, runner
from app.llm.gateway import ModelGateway
from app.llm.mock import MockModelAdapter
from app.models import (
    Agent,
    AgentStatus,
    AgentVersion,
    AutonomyPolicy,
    HumanIntervention,
    InterventionStatus,
    InterventionSubject,
    InterventionType,
    OutboxEvent,
    PauseReason,
    TaskStatus,
    Workflow,
    WorkflowStatus,
    WorkflowTask,
)
from app.planning import analysis, plan
from app.services.usage import record_model_call
from app.verification import rework
from app.workflows import engine
from app.workflows.state_machine import Actor
from tests.conftest import make_settings
from tests.integration.api_helpers import (
    API,
    ClientFactory,
    csrf,
    member,
    my_workspace,
    new_email,
    register,
)

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
ACTION = "Email the customer list"
_NO_MODEL = ModelGateway(
    make_settings(), MockModelAdapter(defaults={}), lambda record: uuid.uuid4()
)


@pytest.fixture
def owner(new_client: ClientFactory) -> TestClient:
    client = new_client()
    assert register(client, new_email()).status_code == 201
    return client


def _user_id(client: TestClient) -> str:
    user_id: str = client.get(f"{API}/auth/me").json()["user"]["id"]
    return user_id


def _url(client: TestClient, suffix: str = "") -> str:
    return f"{API}/workspaces/{my_workspace(client)}/interventions{suffix}"


def _wf_url(client: TestClient, suffix: str = "") -> str:
    return f"{API}/workspaces/{my_workspace(client)}/workflows{suffix}"


def _requests(client: TestClient, **query: str) -> list[dict[str, Any]]:
    response = client.get(_url(client), params=query)
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


def _events(client: TestClient, workflow_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = client.get(
        _wf_url(client, f"/{workflow_id}/events"), params={"limit": 500}
    ).json()["items"]
    return items


def _move(client: TestClient, workflow_id: str, to: str) -> int:
    revision = client.get(_wf_url(client, f"/{workflow_id}")).json()["revision"]
    response = client.post(
        _wf_url(client, f"/{workflow_id}/transitions"),
        json={"to_status": to, "revision": revision},
        headers=csrf(client),
    )
    return response.status_code


# --- plan approval --------------------------------------------------------------------------


def _awaiting_approval(client: TestClient) -> str:
    created = client.post(
        _wf_url(client), json={"prompt": "Research AI support platforms"}, headers=csrf(client)
    ).json()
    assert created["autonomy_policy"] == "approval_required"
    added = client.post(
        _wf_url(client, f"/{created['id']}/tasks"),
        json={"title": "Research", "required_skills": ["report_writing"]},
        headers=csrf(client),
    )
    assert added.status_code == 201, added.text
    assert _move(client, created["id"], "PLANNING") == 200
    assert _move(client, created["id"], "AWAITING_APPROVAL") == 200
    workflow_id: str = created["id"]
    return workflow_id


def test_a_plan_awaiting_approval_is_a_request_only_admins_decide(
    owner: TestClient, new_client: ClientFactory
) -> None:
    workflow_id = _awaiting_approval(owner)
    viewer = member(new_client, owner, "VIEWER")
    editor = member(new_client, owner, "MEMBER")

    (request,) = _requests(viewer, workflow_id=workflow_id, status="OPEN")
    assert (request["type"], request["subject"], request["requested_by"]) == (
        "APPROVAL",
        "plan_approval",
        "SYSTEM",
    )
    assert request["workflow_prompt"] == "Research AI support platforms"
    assert viewer.get(_url(viewer, f"/{request['id']}")).json()["id"] == request["id"]

    for client in (viewer, editor):
        refused = client.post(_url(client, f"/{request['id']}/approve"), headers=csrf(client))
        assert refused.status_code == 403
    approved = owner.post(_url(owner, f"/{request['id']}/approve"), headers=csrf(owner))
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "RESOLVED" and body["resolution"] == {"decision": "approve"}
    assert body["resolved_by_user_id"] == _user_id(owner)
    assert owner.get(_wf_url(owner, f"/{workflow_id}")).json()["status"] == "READY"

    again = owner.post(_url(owner, f"/{request['id']}/reject"), headers=csrf(owner))
    assert again.status_code == 409 and again.json()["error"]["code"] == "INTERVENTION_CLOSED"
    kinds = [e["event_type"] for e in _events(owner, workflow_id)]
    assert kinds.count("INTERVENTION_OPENED") == 1 and kinds.count("INTERVENTION_RESOLVED") == 1


def test_the_workflow_transitions_resolve_or_withdraw_the_plan_request(
    owner: TestClient,
) -> None:
    workflow_id = _awaiting_approval(owner)
    (first,) = _requests(owner, workflow_id=workflow_id, status="OPEN")

    # Sending the plan back from the workflow page is the same decision, attributed.
    assert _move(owner, workflow_id, "PLANNING") == 200
    rejected = owner.get(_url(owner, f"/{first['id']}")).json()
    assert rejected["status"] == "RESOLVED" and rejected["resolution"] == {"decision": "reject"}
    assert rejected["resolved_by_user_id"] == _user_id(owner)

    # Asking again opens a new request; cancelling the workflow withdraws it.
    assert _move(owner, workflow_id, "AWAITING_APPROVAL") == 200
    (second,) = _requests(owner, workflow_id=workflow_id, status="OPEN")
    assert second["id"] != first["id"]
    cancelled = owner.post(
        _wf_url(owner, f"/{workflow_id}/cancel"),
        json={"revision": owner.get(_wf_url(owner, f"/{workflow_id}")).json()["revision"]},
        headers=csrf(owner),
    )
    assert cancelled.status_code == 200, cancelled.text
    withdrawn = owner.get(_url(owner, f"/{second['id']}")).json()
    assert withdrawn["status"] == "WITHDRAWN" and withdrawn["resolved_by_user_id"] is None
    assert _requests(owner, workflow_id=workflow_id, status="OPEN") == []


def test_requests_are_invisible_across_workspaces(
    owner: TestClient, new_client: ClientFactory
) -> None:
    workflow_id = _awaiting_approval(owner)
    (request,) = _requests(owner, workflow_id=workflow_id)
    mallory = new_client()
    register(mallory, new_email())

    assert _requests(mallory) == []
    assert mallory.get(_url(mallory, f"/{request['id']}")).status_code == 404
    other = mallory.post(_url(mallory, f"/{request['id']}/approve"), headers=csrf(mallory))
    assert other.status_code == 404 and other.json()["error"]["code"] == "INTERVENTION_NOT_FOUND"
    foreign = f"{API}/workspaces/{my_workspace(owner)}/interventions"
    assert mallory.get(foreign).status_code == 404


# --- analysis questions ---------------------------------------------------------------------


def test_analysis_questions_are_answered_by_members_and_reach_the_planner(
    owner: TestClient,
    new_client: ClientFactory,
    admin_factory: Factory,
    worker_factory: Factory,
    api_settings: Settings,
) -> None:
    created = owner.post(
        _wf_url(owner), json={"prompt": "Write a report."}, headers=csrf(owner)
    ).json()
    assert _move(owner, created["id"], "PLANNING") == 200
    gateway = ModelGateway(
        api_settings, MockModelAdapter(), partial(record_model_call, worker_factory)
    )
    with session_scope(admin_factory) as s:
        request = s.scalars(
            select(OutboxEvent).where(
                OutboxEvent.workflow_id == uuid.UUID(created["id"]),
                OutboxEvent.event_type == outbox.WORKFLOW_ANALYZE,
            )
        ).one()
    with session_scope(worker_factory) as s:
        job = analysis.claim(
            s,
            event_id=request.id,
            workspace_id=request.workspace_id,
            workflow_id=uuid.UUID(created["id"]),
        )
    assert isinstance(job, analysis.AnalysisJob)
    outcome = analysis.analyse(gateway, job, sleep=lambda _: None)
    with session_scope(worker_factory) as s:
        assert analysis.finish(s, job, outcome) == "ready"

    # The mock analysis asks one question about a brief prompt.
    (question,) = _requests(owner, workflow_id=created["id"], status="OPEN")
    assert (question["type"], question["subject"]) == ("CLARIFICATION", "analysis_question")
    assert question["requested_action"].startswith("The request is brief.")
    assert question["context"]["ambiguity_id"] == "a1"

    viewer = member(new_client, owner, "VIEWER")
    editor = member(new_client, owner, "MEMBER")
    answer_url = _url(owner, f"/{question['id']}/answer")
    refused = viewer.post(answer_url, json={"answer": "x"}, headers=csrf(viewer))
    assert refused.status_code == 403
    mismatch = owner.post(_url(owner, f"/{question['id']}/approve"), headers=csrf(owner))
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "INTERVENTION_TYPE_MISMATCH"
    empty = editor.post(answer_url, json={"answer": "   "}, headers=csrf(editor))
    assert empty.status_code == 422
    answered = editor.post(
        answer_url, json={"answer": "  Two pages, for executives.  "}, headers=csrf(editor)
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["resolution"] == {"answer": "Two pages, for executives."}
    assert answered.json()["resolved_by_user_id"] == _user_id(editor)
    resolved = [
        e for e in _events(owner, created["id"]) if e["event_type"] == "INTERVENTION_RESOLVED"
    ]
    assert "executives" not in str(resolved)  # events carry identifiers, never answers

    # The plan job takes the answers when it claims (prompt v2).
    with session_scope(admin_factory) as s:
        plan_request = s.scalars(
            select(OutboxEvent).where(
                OutboxEvent.workflow_id == uuid.UUID(created["id"]),
                OutboxEvent.event_type == outbox.WORKFLOW_PLAN,
            )
        ).one()
    with session_scope(worker_factory) as s:
        plan_job = plan.claim(
            s,
            event_id=plan_request.id,
            workspace_id=plan_request.workspace_id,
            workflow_id=uuid.UUID(created["id"]),
        )
    assert isinstance(plan_job, plan.PlanJob)
    user_message = plan_job.request.messages[1].content
    assert "<clarifications>" in user_message and "Two pages, for executives." in user_message
    assert plan.PROMPT_VERSION == "plan_generation/v2"


def test_a_newer_analysis_withdraws_the_open_questions_of_the_old_one(
    owner: TestClient, admin_factory: Factory, worker_factory: Factory
) -> None:
    created = owner.post(
        _wf_url(owner), json={"prompt": "Write a report."}, headers=csrf(owner)
    ).json()
    workflow_id = uuid.UUID(created["id"])
    with session_scope(admin_factory) as s:
        workflow = s.get_one(Workflow, workflow_id)
        for request_id, question in ((uuid.uuid4(), "Old?"), (uuid.uuid4(), "New?")):
            workflow.analysis_request_id = request_id
            engine.close_interventions(
                s,
                workflow,
                status=InterventionStatus.WITHDRAWN,
                resolution={"outcome": "superseded by a newer analysis"},
            )
            s.flush()
            engine.open_intervention(
                s,
                workflow,
                type=InterventionType.CLARIFICATION,
                subject=InterventionSubject.ANALYSIS_QUESTION,
                requested_action=question,
                context={"analysis_request_id": str(request_id)},
                source_key=f"clarification:{request_id}:a1",
            )
            s.flush()
        s.commit()
    statuses = {r["requested_action"]: r["status"] for r in _requests(owner)}
    assert statuses == {"Old?": "WITHDRAWN", "New?": "OPEN"}
    with session_scope(worker_factory) as s:
        # Only answers to the current analysis's questions reach the planner.
        assert plan.answered_clarifications(s, s.get_one(Workflow, workflow_id)) == []


# --- agents asking for approval -------------------------------------------------------------


class _AskingAgent(MockAgent):
    """Asks for approval on its first try; afterwards submits normally."""

    def execute(self, contract: TaskContract, context: AgentContext) -> AgentSubmission:
        submission = super().execute(contract, context)
        if contract.approved_actions or contract.rework_instructions:
            return submission
        request = ApprovalRequest(
            action=ACTION,
            reason="The brief asks for outreach.",
            risks=["Customers may be contacted twice."],
            consequences=["Emails cannot be recalled."],
        )
        return submission.model_copy(update={"approval_request": request})


@dataclass(frozen=True)
class World:
    workspace_id: uuid.UUID
    workflow_id: uuid.UUID
    task_id: uuid.UUID
    agent_id: uuid.UUID


def _running(
    admin: Factory,
    client: TestClient,
    *,
    attempt_count: int = 0,
    policy: AutonomyPolicy = AutonomyPolicy.AUTONOMOUS,
) -> World:
    """A RUNNING workflow in the client's workspace with one task, pre-assigned and
    dispatched."""
    workspace_id = uuid.UUID(my_workspace(client))
    with session_scope(admin) as s:
        agent = Agent(
            workspace_id=workspace_id, name=f"Writer {uuid.uuid4().hex[:6]}", category="w"
        )
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
            created_by_user_id=uuid.UUID(_user_id(client)),
            prompt="p",
            autonomy_policy=policy,
        )
        s.add(workflow)
        s.flush()
        task = WorkflowTask(
            workspace_id=workspace_id,
            workflow_id=workflow.id,
            title="A",
            description="d",
            required_skills=["report_writing"],
            deliverables=["Report"],
            acceptance_criteria=[{"id": "c1", "description": "Covers the topic."}],
            attempt_count=attempt_count,
            assigned_agent_id=agent.id,
            assigned_agent_version_id=version.id,
        )
        s.add(task)
        s.flush()
        path = [WorkflowStatus.PLANNING, WorkflowStatus.READY, WorkflowStatus.RUNNING]
        if policy == AutonomyPolicy.APPROVAL_REQUIRED:
            path.insert(1, WorkflowStatus.AWAITING_APPROVAL)
        for target in path:
            engine.transition_workflow(s, workflow, target, actor=Actor.USER)
        s.commit()
        return World(workspace_id, workflow.id, task.id, agent.id)


def _claim(admin: Factory, worker: Factory, world: World) -> runner.Claim:
    with session_scope(admin) as s:
        event = s.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.event_type == outbox.TASK_DISPATCH,
                OutboxEvent.task_id == world.task_id,
            )
            .order_by(OutboxEvent.created_at.desc())
        ).first()
    assert event is not None
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


def _ask(admin: Factory, worker: Factory, world: World) -> runner.Claim:
    claim = _claim(admin, worker, world)
    outcome = runner.run_agent(claim, _AskingAgent, models=_NO_MODEL, is_current=lambda: True)
    with session_scope(worker) as s:
        assert runner.finish(s, claim, outcome) == "waiting_approval"
    return claim


def _task(admin: Factory, world: World) -> WorkflowTask:
    with session_scope(admin) as s:
        return s.get_one(WorkflowTask, world.task_id)


def _intervention(admin: Factory, world: World, subject: str) -> HumanIntervention:
    with session_scope(admin) as s:
        return s.scalars(
            select(HumanIntervention)
            .where(
                HumanIntervention.task_id == world.task_id,
                HumanIntervention.subject == subject,
            )
            .order_by(HumanIntervention.created_at.desc())
        ).first() or pytest.fail(f"no {subject} request")


def test_an_approved_action_reaches_the_next_attempt_of_the_same_agent(
    owner: TestClient, new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _running(admin_factory, owner)
    first = _ask(admin_factory, worker_factory, world)

    task = _task(admin_factory, world)
    assert task.status == TaskStatus.WAITING_APPROVAL and task.attempt_count == 0
    (request,) = _requests(owner, workflow_id=str(world.workflow_id), status="OPEN")
    assert (request["type"], request["subject"], request["requested_by"]) == (
        "APPROVAL",
        "agent_action",
        "AGENT",
    )
    assert request["requested_by_agent_id"] == str(world.agent_id)
    assert request["requested_action"] == ACTION
    assert request["context"]["risks"] == ["Customers may be contacted twice."]
    assert request["context"]["if_approved"] == runner.APPROVED_NEXT
    assert request["context"]["execution_id"] == str(first.execution_id)
    # ADR-0037: an agent's request expires after the default 24 hours.
    expires = datetime.fromisoformat(request["expires_at"])
    assert timedelta(hours=23) < expires - datetime.now(UTC) <= timedelta(hours=24)
    step = owner.get(_wf_url(owner, f"/{world.workflow_id}")).json()["next_step"]
    assert step is None or step["kind"] in {"approve_actions", "running"}

    editor = member(new_client, owner, "MEMBER")
    refused = editor.post(_url(editor, f"/{request['id']}/approve"), headers=csrf(editor))
    assert refused.status_code == 403
    approved = owner.post(_url(owner, f"/{request['id']}/approve"), headers=csrf(owner))
    assert approved.status_code == 200, approved.text

    task = _task(admin_factory, world)
    assert task.status == TaskStatus.ASSIGNED and task.attempt_count == 1
    second = _claim(admin_factory, worker_factory, world)
    assert second.attempt_number == 2 and second.agent_id == world.agent_id
    assert second.approved_actions == [ACTION] and second.rework_instructions == []
    events = _events(owner, str(world.workflow_id))
    assert ACTION not in str([e for e in events if e["event_type"].startswith("INTERVENTION")])


def test_a_rejection_retries_with_a_fixed_instruction(
    owner: TestClient, admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _running(admin_factory, owner)
    _ask(admin_factory, worker_factory, world)
    request = _intervention(admin_factory, world, "agent_action")

    rejected = owner.post(_url(owner, f"/{request.id}/reject"), headers=csrf(owner))
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["resolution"] == {"decision": "reject"}

    second = _claim(admin_factory, worker_factory, world)
    assert second.approved_actions == []
    assert second.rework_instructions == [rework.REJECTED_INSTRUCTION]
    outcome = runner.run_agent(second, _AskingAgent, models=_NO_MODEL, is_current=lambda: True)
    with session_scope(worker_factory) as s:
        assert runner.finish(s, second, outcome) == "submitted"


def test_a_decision_on_the_last_attempt_escalates_and_people_resolve_it_by_acting(
    owner: TestClient, admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _running(admin_factory, owner, attempt_count=runner.MAX_ATTEMPTS_PER_TASK - 1)
    claim = _ask(admin_factory, worker_factory, world)
    assert claim.attempt_number == runner.MAX_ATTEMPTS_PER_TASK
    request = _intervention(admin_factory, world, "agent_action")

    approved = owner.post(_url(owner, f"/{request.id}/approve"), headers=csrf(owner))
    assert approved.status_code == 200, approved.text
    assert _task(admin_factory, world).status == TaskStatus.ESCALATED
    escalation = _intervention(admin_factory, world, "task_escalation")
    assert escalation.type == "MANUAL_RECOVERY" and escalation.status == InterventionStatus.OPEN
    assert escalation.context["reason"] == "ATTEMPTS_EXHAUSTED"

    # Reassigning is acting on the escalation: it is resolved and attributed.
    task = owner.get(_wf_url(owner, f"/{world.workflow_id}/tasks/{world.task_id}")).json()
    assigned = owner.post(
        _wf_url(owner, f"/{world.workflow_id}/tasks/{world.task_id}/assign"),
        json={"agent_id": str(world.agent_id), "revision": task["revision"]},
        headers=csrf(owner),
    )
    assert assigned.status_code == 200, assigned.text
    resolved = _intervention(admin_factory, world, "task_escalation")
    assert resolved.status == InterventionStatus.RESOLVED
    assert resolved.resolution == {"outcome": "ASSIGNED", "actor": "USER"}
    assert str(resolved.resolved_by_user_id) == _user_id(owner)


def test_cancelling_a_task_waiting_for_approval_withdraws_its_request(
    owner: TestClient, admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _running(admin_factory, owner)
    _ask(admin_factory, worker_factory, world)
    request = _intervention(admin_factory, world, "agent_action")
    task = owner.get(_wf_url(owner, f"/{world.workflow_id}/tasks/{world.task_id}")).json()

    cancelled = owner.post(
        _wf_url(owner, f"/{world.workflow_id}/tasks/{world.task_id}/cancel"),
        json={"revision": task["revision"]},
        headers=csrf(owner),
    )
    assert cancelled.status_code == 200, cancelled.text
    withdrawn = _intervention(admin_factory, world, "agent_action")
    assert withdrawn.id == request.id and withdrawn.status == InterventionStatus.WITHDRAWN
    late = owner.post(_url(owner, f"/{request.id}/approve"), headers=csrf(owner))
    assert late.status_code == 409


def test_the_worker_role_can_open_and_close_requests_but_not_delete_them(
    worker_factory: Factory, owner: TestClient, admin_factory: Factory
) -> None:
    world = _running(admin_factory, owner)
    _ask(admin_factory, worker_factory, world)
    with session_scope(worker_factory) as s:
        row = s.scalars(
            select(HumanIntervention).where(HumanIntervention.task_id == world.task_id)
        ).one()
        s.delete(row)
        with pytest.raises(Exception, match="permission denied"):
            s.flush()


# --- pausing for decisions (Step 6.2, ADR-0036) --------------------------------------------


def _workflow(admin: Factory, world: World) -> Workflow:
    with session_scope(admin) as s:
        return s.get_one(Workflow, world.workflow_id)


def _dispatches(admin: Factory, world: World) -> int:
    with session_scope(admin) as s:
        return len(
            s.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == outbox.TASK_DISPATCH,
                    OutboxEvent.task_id == world.task_id,
                )
            ).all()
        )


def test_an_approval_request_pauses_an_approval_workflow_until_the_decision(
    owner: TestClient, admin_factory: Factory, worker_factory: Factory
) -> None:
    # Handoff critical scenario 10: human approval pauses and resumes a workflow.
    world = _running(admin_factory, owner, policy=AutonomyPolicy.APPROVAL_REQUIRED)
    _ask(admin_factory, worker_factory, world)

    paused = owner.get(_wf_url(owner, f"/{world.workflow_id}")).json()
    assert paused["status"] == "PAUSED" and paused["pause_reason"] == "AWAITING_DECISION"
    assert paused["paused_at"] and paused["paused_by_user_id"] is None
    request = _intervention(admin_factory, world, "agent_action")

    approved = owner.post(_url(owner, f"/{request.id}/approve"), headers=csrf(owner))
    assert approved.status_code == 200, approved.text

    resumed = owner.get(_wf_url(owner, f"/{world.workflow_id}")).json()
    assert resumed["status"] == "RUNNING" and resumed["pause_reason"] is None
    assert resumed["paused_at"] is None
    assert _dispatches(admin_factory, world) == 2  # the next attempt is dispatched at once
    changes = [
        e["payload"]
        for e in _events(owner, str(world.workflow_id))
        if e["event_type"] == "WORKFLOW_STATUS_CHANGED" and e["task_id"] is None
    ]
    assert changes[-2]["to"] == "PAUSED" and changes[-2]["reason"] == engine.AWAITING_DECISION
    assert changes[-1] == {
        "from": "PAUSED",
        "to": "RUNNING",
        "actor": "SYSTEM",
        "reason": engine.DECISIONS_MADE,
        "pause_reason": "AWAITING_DECISION",
    }


def test_autonomous_workflows_keep_running_while_one_task_waits(
    owner: TestClient, admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _running(admin_factory, owner)
    _ask(admin_factory, worker_factory, world)
    assert _workflow(admin_factory, world).status == WorkflowStatus.RUNNING


def test_the_platform_never_lifts_a_person_s_pause(
    owner: TestClient, admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _running(admin_factory, owner, policy=AutonomyPolicy.APPROVAL_REQUIRED)
    _ask(admin_factory, worker_factory, world)
    # A person resumes by hand, then pauses again with a note: the pause is now theirs.
    assert _move(owner, str(world.workflow_id), "RUNNING") == 200
    revision = owner.get(_wf_url(owner, f"/{world.workflow_id}")).json()["revision"]
    paused = owner.post(
        _wf_url(owner, f"/{world.workflow_id}/transitions"),
        json={"to_status": "PAUSED", "revision": revision, "reason": "Waiting for legal review"},
        headers=csrf(owner),
    ).json()
    assert paused["pause_reason"] == "USER" and paused["paused_by_user_id"] == _user_id(owner)
    assert paused["pause_note"] == "Waiting for legal review"
    request = _intervention(admin_factory, world, "agent_action")

    assert owner.post(_url(owner, f"/{request.id}/approve"), headers=csrf(owner)).status_code == 200

    still = _workflow(admin_factory, world)
    assert still.status == WorkflowStatus.PAUSED and still.pause_reason == PauseReason.USER
    assert _task(admin_factory, world).status == TaskStatus.ASSIGNED
    assert _dispatches(admin_factory, world) == 1  # nothing runs until people resume
    assert _move(owner, str(world.workflow_id), "RUNNING") == 200
    assert _dispatches(admin_factory, world) == 2


def test_cancelling_the_waiting_task_also_resumes_the_workflow(
    owner: TestClient, admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _running(admin_factory, owner, policy=AutonomyPolicy.APPROVAL_REQUIRED)
    _ask(admin_factory, worker_factory, world)
    task = owner.get(_wf_url(owner, f"/{world.workflow_id}/tasks/{world.task_id}")).json()

    cancelled = owner.post(
        _wf_url(owner, f"/{world.workflow_id}/tasks/{world.task_id}/cancel"),
        json={"revision": task["revision"]},
        headers=csrf(owner),
    )
    assert cancelled.status_code == 200, cancelled.text
    # No decision is awaited any more; the workflow then goes on (here: blocked by the
    # cancelled required task, which people resolve).
    workflow = _workflow(admin_factory, world)
    assert workflow.pause_reason is None
    assert workflow.status in {WorkflowStatus.RUNNING, WorkflowStatus.BLOCKED}


# --- expiry and rejection choices (Step 6.3, ADR-0037) --------------------------------------


def _expire(admin: Factory, world: World) -> None:
    with session_scope(admin) as s:
        row = s.scalars(
            select(HumanIntervention).where(HumanIntervention.task_id == world.task_id)
        ).one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        s.commit()


def test_an_expired_request_counts_as_a_rejection_and_resumes_the_workflow(
    owner: TestClient, admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _running(admin_factory, owner, policy=AutonomyPolicy.APPROVAL_REQUIRED)
    _ask(admin_factory, worker_factory, world)
    assert recovery.recover_once(worker_factory).expired_requests == 0  # not due yet
    assert _workflow(admin_factory, world).pause_reason == PauseReason.AWAITING_DECISION

    _expire(admin_factory, world)
    assert recovery.recover_once(worker_factory) == recovery.RecoveryReport(expired_requests=1)
    assert recovery.recover_once(worker_factory).expired_requests == 0

    expired = _intervention(admin_factory, world, "agent_action")
    assert expired.status == InterventionStatus.EXPIRED and expired.resolved_by_user_id is None
    assert expired.resolution == {"decision": "reject", "outcome": "expired"}
    assert _workflow(admin_factory, world).status == WorkflowStatus.RUNNING
    second = _claim(admin_factory, worker_factory, world)
    assert second.attempt_number == 2 and second.approved_actions == []
    assert second.rework_instructions == [rework.REJECTED_INSTRUCTION]
    listed = _requests(owner, status="EXPIRED")
    assert [r["id"] for r in listed] == [str(expired.id)]


def test_a_request_decided_in_time_does_not_expire(
    owner: TestClient, admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _running(admin_factory, owner)
    _ask(admin_factory, worker_factory, world)
    request = _intervention(admin_factory, world, "agent_action")
    assert owner.post(_url(owner, f"/{request.id}/approve"), headers=csrf(owner)).status_code == 200
    _expire(admin_factory, world)

    assert recovery.recover_once(worker_factory).expired_requests == 0
    assert _intervention(admin_factory, world, "agent_action").status == InterventionStatus.RESOLVED


def test_an_admin_can_reject_and_stop_the_task_with_a_note_for_people(
    owner: TestClient, new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _running(admin_factory, owner)
    _ask(admin_factory, worker_factory, world)
    request = _intervention(admin_factory, world, "agent_action")
    reject_url = _url(owner, f"/{request.id}/reject")
    editor = member(new_client, owner, "MEMBER")
    assert editor.post(reject_url, json={"then": "stop"}, headers=csrf(editor)).status_code == 403
    bad = owner.post(reject_url, json={"then": "cancel"}, headers=csrf(owner))
    assert bad.status_code == 422

    note = "Not with customer data; use the anonymised list."
    rejected = owner.post(reject_url, json={"then": "stop", "note": note}, headers=csrf(owner))
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["resolution"] == {"decision": "reject", "then": "stop", "note": note}

    task = _task(admin_factory, world)
    assert task.status == TaskStatus.ESCALATED and task.attempt_count == 1
    escalation = _intervention(admin_factory, world, "task_escalation")
    assert escalation.status == InterventionStatus.OPEN
    assert escalation.context["reason"] == engine.APPROVAL_REJECTED
    assert _dispatches(admin_factory, world) == 1  # nothing runs until people decide
    events = str(_events(owner, str(world.workflow_id)))
    assert "anonymised" not in events  # the note stays on the request
    resolved = [
        e
        for e in _events(owner, str(world.workflow_id))
        if e["event_type"] == "INTERVENTION_RESOLVED"
    ]
    assert resolved[-1]["payload"]["then"] == "stop"


def test_plan_rejections_take_a_note_but_cannot_stop_a_task(owner: TestClient) -> None:
    workflow_id = _awaiting_approval(owner)
    (request,) = _requests(owner, workflow_id=workflow_id, status="OPEN")
    url = _url(owner, f"/{request['id']}/reject")

    stopped = owner.post(url, json={"then": "stop"}, headers=csrf(owner))
    assert stopped.status_code == 409
    assert stopped.json()["error"]["code"] == "INTERVENTION_TYPE_MISMATCH"
    rejected = owner.post(url, json={"note": "Split the research task."}, headers=csrf(owner))
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["resolution"] == {
        "decision": "reject",
        "note": "Split the research task.",
    }
    assert owner.get(_wf_url(owner, f"/{workflow_id}")).json()["status"] == "PLANNING"
