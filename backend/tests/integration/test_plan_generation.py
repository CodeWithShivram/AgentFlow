"""Plan drafts (Step 2.3) in PostgreSQL: chained after analysis, regenerate, failure, safety."""

import uuid
from collections.abc import Callable
from functools import partial
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.sync_session import session_scope
from app.execution import outbox
from app.llm.gateway import ModelGateway
from app.llm.mock import MockModelAdapter
from app.models import LlmUsage, OutboxEvent, WorkflowPlan
from app.planning import analysis, plan
from app.services.usage import record_model_call
from tests.integration.api_helpers import (
    API,
    ClientFactory,
    csrf,
    member,
    my_workspace,
    new_email,
    register,
    sql,
)

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
PROMPT = "Research three CRM vendors. Compare their pricing. Write a short report."


@pytest.fixture
def owner(new_client: ClientFactory) -> TestClient:
    client = new_client()
    assert register(client, new_email()).status_code == 201
    return client


def _url(client: TestClient, workflow_id: str = "", suffix: str = "") -> str:
    base = f"{API}/workspaces/{my_workspace(client)}/workflows"
    return f"{base}/{workflow_id}{suffix}" if workflow_id else base


def _requests(admin: Factory, workflow_id: str, event_type: str) -> list[OutboxEvent]:
    with session_scope(admin) as s:
        return list(
            s.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.workflow_id == uuid.UUID(workflow_id),
                    OutboxEvent.event_type == event_type,
                )
                .order_by(OutboxEvent.created_at)
            )
        )


@pytest.fixture
def jobs(api_settings: Settings, worker_factory: Factory) -> dict[str, Callable[..., str]]:
    """Runs the analysis or plan job for one outbox event, as the restricted worker role."""

    def gateway(adapter: MockModelAdapter | None) -> ModelGateway:
        recorder = partial(record_model_call, worker_factory)
        return ModelGateway(api_settings, adapter or MockModelAdapter(), recorder)

    def run_analysis(event: OutboxEvent, adapter: MockModelAdapter | None = None) -> str:
        assert event.workflow_id is not None
        with session_scope(worker_factory) as s:
            job = analysis.claim(
                s, event_id=event.id, workspace_id=event.workspace_id, workflow_id=event.workflow_id
            )
        if isinstance(job, analysis.Skipped):
            return job.value
        outcome = analysis.analyse(gateway(adapter), job, sleep=lambda _: None)
        with session_scope(worker_factory) as s:
            return analysis.finish(s, job, outcome)

    def run_plan(event: OutboxEvent, adapter: MockModelAdapter | None = None) -> str:
        assert event.workflow_id is not None
        with session_scope(worker_factory) as s:
            job = plan.claim(
                s, event_id=event.id, workspace_id=event.workspace_id, workflow_id=event.workflow_id
            )
        if isinstance(job, plan.Skipped):
            return job.value
        outcome = plan.generate(gateway(adapter), job, sleep=lambda _: None)
        with session_scope(worker_factory) as s:
            return plan.finish(s, job, outcome)

    return {"analysis": run_analysis, "plan": run_plan}


def _analysed_workflow(
    client: TestClient, admin: Factory, jobs: dict[str, Callable[..., str]]
) -> dict[str, Any]:
    """A workflow in PLANNING whose analysis is READY and whose plan request is pending."""
    created = client.post(_url(client), json={"prompt": PROMPT}, headers=csrf(client)).json()
    client.post(
        _url(client, created["id"], "/transitions"),
        json={"to_status": "PLANNING", "revision": created["revision"]},
        headers=csrf(client),
    )
    (request,) = _requests(admin, created["id"], outbox.WORKFLOW_ANALYZE)
    assert jobs["analysis"](request) == "ready"
    body: dict[str, Any] = client.get(_url(client, created["id"])).json()
    return body


def _drafts(admin: Factory, workflow_id: str) -> list[WorkflowPlan]:
    with session_scope(admin) as s:
        return list(
            s.scalars(
                select(WorkflowPlan)
                .where(WorkflowPlan.workflow_id == uuid.UUID(workflow_id))
                .order_by(WorkflowPlan.version)
            )
        )


def test_a_ready_analysis_produces_a_versioned_plan_draft(
    owner: TestClient, admin_factory: Factory, jobs: dict[str, Callable[..., str]]
) -> None:
    workflow = _analysed_workflow(owner, admin_factory, jobs)
    assert workflow["plan_status"] == "PENDING"
    (request,) = _requests(admin_factory, workflow["id"], outbox.WORKFLOW_PLAN)
    assert request.payload == {
        "workspace_id": str(request.workspace_id),
        "workflow_id": workflow["id"],
    }

    assert jobs["plan"](request) == "ready"

    (draft,) = _drafts(admin_factory, workflow["id"])
    proposal = plan.PlanProposal.model_validate(draft.plan)
    assert (draft.version, draft.task_count, draft.request_id) == (
        1,
        len(proposal.tasks),
        request.id,
    )
    assert draft.prompt_version == plan.PROMPT_VERSION
    # Three requirements run in parallel, then a consolidation task (mock provider).
    assert [t.depends_on for t in proposal.tasks] == [[], [], [], ["t1", "t2", "t3"]]

    stored = owner.get(_url(owner, workflow["id"])).json()
    assert stored["plan_status"] == "READY"
    assert stored["revision"] == workflow["revision"]  # drafts never touch the plan
    assert owner.get(_url(owner, workflow["id"], "/tasks")).json()["total"] == 0
    listed = owner.get(_url(owner, workflow["id"], "/plan-drafts")).json()["items"]
    assert [(d["version"], d["task_count"]) for d in listed] == [(1, 4)]
    last = owner.get(_url(owner, workflow["id"], "/events")).json()["items"][-1]
    assert last["event_type"] == "PLAN_GENERATED"
    assert last["payload"]["version"] == 1 and last["payload"]["tasks"] == 4
    with session_scope(admin_factory) as s:
        purposes = sorted(s.scalars(select(LlmUsage.purpose)))
    assert purposes == ["plan_generation", "prompt_analysis"]


def test_regenerating_keeps_earlier_drafts(
    owner: TestClient, admin_factory: Factory, jobs: dict[str, Callable[..., str]]
) -> None:
    workflow = _analysed_workflow(owner, admin_factory, jobs)
    jobs["plan"](_requests(admin_factory, workflow["id"], outbox.WORKFLOW_PLAN)[0])

    again = owner.post(_url(owner, workflow["id"], "/plan-drafts"), headers=csrf(owner))
    assert again.status_code == 200
    assert again.json()["plan_status"] == "PENDING"
    assert again.json()["revision"] == workflow["revision"]
    refused = owner.post(_url(owner, workflow["id"], "/plan-drafts"), headers=csrf(owner))
    assert refused.status_code == 409  # one draft at a time
    assert refused.json()["error"]["code"] == "PLAN_NOT_REGENERATABLE"

    jobs["plan"](_requests(admin_factory, workflow["id"], outbox.WORKFLOW_PLAN)[1])

    listed = owner.get(_url(owner, workflow["id"], "/plan-drafts")).json()["items"]
    assert [d["version"] for d in listed] == [2, 1]


def test_failures_are_recorded_and_can_be_regenerated(
    owner: TestClient, admin_factory: Factory, jobs: dict[str, Callable[..., str]]
) -> None:
    workflow = _analysed_workflow(owner, admin_factory, jobs)
    broken = MockModelAdapter(defaults={})
    broken.script(plan.PURPOSE, "not json", "{}", '{"summary": "s", "tasks": []}')

    assert (
        jobs["plan"](_requests(admin_factory, workflow["id"], outbox.WORKFLOW_PLAN)[0], broken)
        == "failed"
    )

    assert owner.get(_url(owner, workflow["id"])).json()["plan_status"] == "FAILED"
    assert _drafts(admin_factory, workflow["id"]) == []
    last = owner.get(_url(owner, workflow["id"], "/events")).json()["items"][-1]
    assert last["event_type"] == "PLAN_GENERATION_FAILED"
    assert last["payload"]["error_code"] == "MODEL_OUTPUT_INVALID"
    assert (
        owner.post(_url(owner, workflow["id"], "/plan-drafts"), headers=csrf(owner)).status_code
        == 200
    )


def test_a_plan_needs_a_ready_analysis(owner: TestClient) -> None:
    created = owner.post(_url(owner), json={"prompt": PROMPT}, headers=csrf(owner)).json()
    owner.post(
        _url(owner, created["id"], "/transitions"),
        json={"to_status": "PLANNING", "revision": created["revision"]},
        headers=csrf(owner),
    )

    response = owner.post(_url(owner, created["id"], "/plan-drafts"), headers=csrf(owner))

    assert response.status_code == 409  # the analysis is still pending


def test_superseded_and_duplicate_deliveries_change_nothing(
    owner: TestClient, admin_factory: Factory, jobs: dict[str, Callable[..., str]]
) -> None:
    workflow = _analysed_workflow(owner, admin_factory, jobs)
    first = _requests(admin_factory, workflow["id"], outbox.WORKFLOW_PLAN)[0]
    broken = MockModelAdapter(defaults={})
    broken.script(plan.PURPOSE, "x", "x", "x")
    jobs["plan"](first, broken)
    owner.post(_url(owner, workflow["id"], "/plan-drafts"), headers=csrf(owner))

    assert jobs["plan"](first) == "duplicate"
    with session_scope(admin_factory) as s:
        copy_id = outbox.enqueue(
            s,
            outbox.WORKFLOW_PLAN,
            workspace_id=first.workspace_id,
            workflow_id=first.workflow_id,
            payload=first.payload,
        )
        s.commit()
        copy = s.get(OutboxEvent, copy_id)
    assert copy is not None
    assert jobs["plan"](copy) == "stale"
    assert _drafts(admin_factory, workflow["id"]) == []


def test_drafts_are_immutable(
    owner: TestClient,
    admin_factory: Factory,
    jobs: dict[str, Callable[..., str]],
    api_settings: Settings,
) -> None:
    workflow = _analysed_workflow(owner, admin_factory, jobs)
    jobs["plan"](_requests(admin_factory, workflow["id"], outbox.WORKFLOW_PLAN)[0])

    with pytest.raises(DBAPIError, match="immutable"):
        sql(api_settings, "UPDATE workflow_plans SET task_count = 1")


def test_viewers_see_drafts_but_cannot_request_them(
    owner: TestClient,
    new_client: ClientFactory,
    admin_factory: Factory,
    jobs: dict[str, Callable[..., str]],
) -> None:
    workflow = _analysed_workflow(owner, admin_factory, jobs)
    jobs["plan"](_requests(admin_factory, workflow["id"], outbox.WORKFLOW_PLAN)[0])
    viewer = member(new_client, owner, "VIEWER")
    url = f"{API}/workspaces/{my_workspace(owner)}/workflows/{workflow['id']}/plan-drafts"

    assert viewer.get(url).json()["items"][0]["version"] == 1
    assert viewer.post(url, headers=csrf(viewer)).status_code == 403


# --- preview with DAG validation (Step 2.4) ---------------------------------------------------


def _drafted(owner: TestClient, admin: Factory, jobs: dict[str, Callable[..., str]]) -> str:
    workflow = _analysed_workflow(owner, admin, jobs)
    assert jobs["plan"](_requests(admin, workflow["id"], outbox.WORKFLOW_PLAN)[0]) == "ready"
    workflow_id: str = workflow["id"]
    return workflow_id


def test_the_preview_shows_the_full_draft_with_stages(
    owner: TestClient,
    admin_factory: Factory,
    jobs: dict[str, Callable[..., str]],
    seeded_builtins: Settings,
) -> None:
    workflow_id = _drafted(owner, admin_factory, jobs)

    preview = owner.get(_url(owner, workflow_id, "/plan-drafts/1")).json()

    assert preview["version"] == 1 and preview["task_count"] == 4
    proposal = plan.PlanProposal.model_validate(preview["plan"])
    assert [t.key for t in proposal.tasks] == ["t1", "t2", "t3", "t4"]
    assert preview["stages"] == [["t1", "t2", "t3"], ["t4"]]
    # Built-in agents are active and cover every planned skill; 3 parallel tasks fit the limit.
    assert preview["issues"] == []


def test_the_preview_warns_about_skills_no_active_agent_has(
    owner: TestClient, admin_factory: Factory, jobs: dict[str, Callable[..., str]]
) -> None:
    workflow_id = _drafted(owner, admin_factory, jobs)  # no agents at all in this database

    issues = owner.get(_url(owner, workflow_id, "/plan-drafts/1")).json()["issues"]

    assert issues and {i["code"] for i in issues} == {"SKILL_NOT_AVAILABLE"}
    assert all(i["severity"] == "warning" and i["task_keys"] for i in issues)


def test_previews_are_scoped_and_versioned(
    owner: TestClient,
    new_client: ClientFactory,
    admin_factory: Factory,
    jobs: dict[str, Callable[..., str]],
) -> None:
    workflow_id = _drafted(owner, admin_factory, jobs)
    viewer = member(new_client, owner, "VIEWER")
    stranger = new_client()
    register(stranger, new_email())
    url = f"{API}/workspaces/{my_workspace(owner)}/workflows/{workflow_id}/plan-drafts"

    assert viewer.get(f"{url}/1").status_code == 200
    missing = owner.get(f"{url}/2")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "PLAN_DRAFT_NOT_FOUND"
    assert owner.get(f"{url}/0").status_code == 422
    assert stranger.get(f"{url}/1").status_code == 404


# --- confirmation (Step 2.5) ------------------------------------------------------------------


def _workflow(owner: TestClient, workflow_id: str) -> dict[str, Any]:
    body: dict[str, Any] = owner.get(_url(owner, workflow_id)).json()
    return body


def _confirm(owner: TestClient, workflow_id: str, version: int = 1, **body: Any) -> Any:
    revision = body.pop("revision", None) or _workflow(owner, workflow_id)["revision"]
    return owner.post(
        _url(owner, workflow_id, f"/plan-drafts/{version}/confirm"),
        json={"revision": revision, **body},
        headers=csrf(owner),
    )


def _tasks(owner: TestClient, workflow_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = owner.get(_url(owner, workflow_id, "/tasks")).json()["items"]
    return items


def test_confirming_a_draft_creates_its_tasks_and_dependencies(
    owner: TestClient,
    admin_factory: Factory,
    jobs: dict[str, Callable[..., str]],
    seeded_builtins: Settings,
) -> None:
    workflow_id = _drafted(owner, admin_factory, jobs)
    before = _workflow(owner, workflow_id)

    response = _confirm(owner, workflow_id)

    assert response.status_code == 200
    after = response.json()
    assert after["confirmed_plan_version"] == 1
    assert after["revision"] > before["revision"]
    assert after["status"] == "PLANNING"
    tasks = _tasks(owner, workflow_id)
    draft = plan.PlanProposal.model_validate(
        owner.get(_url(owner, workflow_id, "/plan-drafts/1")).json()["plan"]
    )
    by_title = {t["title"]: t for t in tasks}
    assert sorted(by_title) == sorted(t.title for t in draft.tasks)
    ids = {planned.key: by_title[planned.title]["id"] for planned in draft.tasks}
    for planned in draft.tasks:
        created = by_title[planned.title]
        assert sorted(created["depends_on"]) == sorted(ids[k] for k in planned.depends_on)
        assert created["required_skills"] == planned.required_skills
        assert created["required"] == planned.required
        assert created["acceptance_criteria"] == [
            c.model_dump() for c in planned.acceptance_criteria
        ]
        assert created["status"] == "PENDING"
    events = owner.get(_url(owner, workflow_id, "/events")).json()["items"]
    confirmed = events[-1]
    assert confirmed["event_type"] == "PLAN_CONFIRMED"
    assert confirmed["payload"]["tasks_created"] == 4
    assert confirmed["payload"]["tasks_removed"] == 0
    keys = {e["payload"].get("plan_task_key") for e in events if e["event_type"] == "TASK_CREATED"}
    assert keys == {"t1", "t2", "t3", "t4"}


def test_warnings_must_be_acknowledged(
    owner: TestClient, admin_factory: Factory, jobs: dict[str, Callable[..., str]]
) -> None:
    workflow_id = _drafted(owner, admin_factory, jobs)  # no active agents: skill warnings

    refused = _confirm(owner, workflow_id)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "PLAN_WARNINGS_NOT_ACKNOWLEDGED"
    assert refused.json()["error"]["details"]["codes"] == ["SKILL_NOT_AVAILABLE"]
    assert _tasks(owner, workflow_id) == []

    accepted = _confirm(owner, workflow_id, acknowledge_warnings=True)
    assert accepted.status_code == 200
    events = owner.get(_url(owner, workflow_id, "/events")).json()["items"]
    assert events[-1]["payload"]["acknowledged_warnings"] == ["SKILL_NOT_AVAILABLE"]


def test_existing_tasks_are_replaced_or_kept_as_the_user_chooses(
    owner: TestClient,
    admin_factory: Factory,
    jobs: dict[str, Callable[..., str]],
    seeded_builtins: Settings,
) -> None:
    workflow_id = _drafted(owner, admin_factory, jobs)
    manual = owner.post(
        _url(owner, workflow_id, "/tasks"), json={"title": "Manual task"}, headers=csrf(owner)
    ).json()

    undecided = _confirm(owner, workflow_id)
    assert undecided.status_code == 409
    assert undecided.json()["error"]["code"] == "EXISTING_TASKS_MODE_REQUIRED"
    assert undecided.json()["error"]["details"]["existing_tasks"] == 1

    assert _confirm(owner, workflow_id, existing_tasks="append").status_code == 200
    appended = _tasks(owner, workflow_id)
    assert len(appended) == 5 and not any(t["removed"] for t in appended)

    assert _confirm(owner, workflow_id, existing_tasks="replace").status_code == 200
    replaced = _tasks(owner, workflow_id)
    active = [t for t in replaced if not t["removed"]]
    removed = [t for t in replaced if t["removed"]]
    assert len(active) == 4
    assert len(removed) == 5 and all(t["status"] == "CANCELLED" for t in removed)
    assert manual["id"] in {t["id"] for t in removed}
    payload = owner.get(_url(owner, workflow_id, "/events")).json()["items"][-1]["payload"]
    assert (payload["existing_tasks"], payload["tasks_removed"]) == ("replace", 5)


def test_confirmation_is_refused_when_stale_unknown_forbidden_or_running(
    owner: TestClient,
    new_client: ClientFactory,
    admin_factory: Factory,
    jobs: dict[str, Callable[..., str]],
    seeded_builtins: Settings,
) -> None:
    workflow_id = _drafted(owner, admin_factory, jobs)
    # Any revision other than the current one is stale client state.
    stale = _workflow(owner, workflow_id)["revision"] + 1

    conflict = _confirm(owner, workflow_id, revision=stale)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "WORKFLOW_REVISION_CONFLICT"
    assert _confirm(owner, workflow_id, version=9).status_code == 404
    viewer = member(new_client, owner, "VIEWER")
    url = f"{API}/workspaces/{my_workspace(owner)}/workflows/{workflow_id}/plan-drafts/1/confirm"
    forbidden = viewer.post(
        url, json={"revision": _workflow(owner, workflow_id)["revision"]}, headers=csrf(viewer)
    )
    assert forbidden.status_code == 403

    # Confirm, then run the workflow: the plan can no longer be replaced.
    assert _confirm(owner, workflow_id).status_code == 200
    for target in ("AWAITING_APPROVAL", "READY", "RUNNING"):
        moved = owner.post(
            _url(owner, workflow_id, "/transitions"),
            json={"to_status": target, "revision": _workflow(owner, workflow_id)["revision"]},
            headers=csrf(owner),
        )
        assert moved.status_code == 200, moved.json()
    running = _confirm(owner, workflow_id, existing_tasks="replace")
    assert running.status_code == 409
    assert running.json()["error"]["code"] == "PLAN_NOT_EDITABLE"


def test_confirming_after_approval_reopens_planning(
    owner: TestClient,
    admin_factory: Factory,
    jobs: dict[str, Callable[..., str]],
    seeded_builtins: Settings,
) -> None:
    workflow_id = _drafted(owner, admin_factory, jobs)
    assert _confirm(owner, workflow_id).status_code == 200
    owner.post(
        _url(owner, workflow_id, "/transitions"),
        json={
            "to_status": "AWAITING_APPROVAL",
            "revision": _workflow(owner, workflow_id)["revision"],
        },
        headers=csrf(owner),
    )
    assert _workflow(owner, workflow_id)["status"] == "AWAITING_APPROVAL"

    again = _confirm(owner, workflow_id, existing_tasks="replace")

    assert again.status_code == 200
    assert again.json()["status"] == "PLANNING"  # the approval request is invalidated
