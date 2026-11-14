"""The orchestration job in PostgreSQL (Step 4.1, ADR-0025), run as the restricted worker role."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.sync_session import session_scope
from app.execution import outbox
from app.models import OutboxEvent
from app.orchestration import job
from tests.integration.api_helpers import (
    API,
    ClientFactory,
    csrf,
    my_workspace,
    new_email,
    register,
)

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]


@pytest.fixture
def owner(new_client: ClientFactory) -> TestClient:
    client = new_client()
    assert register(client, new_email()).status_code == 201
    return client


def _url(client: TestClient, suffix: str = "") -> str:
    return f"{API}/workspaces/{my_workspace(client)}/workflows{suffix}"


def _move(client: TestClient, workflow_id: str, to: str) -> None:
    revision = client.get(_url(client, f"/{workflow_id}")).json()["revision"]
    response = client.post(
        _url(client, f"/{workflow_id}/transitions"),
        json={"to_status": to, "revision": revision},
        headers=csrf(client),
    )
    assert response.status_code == 200, response.text


def _requests(admin: Factory, workflow_id: str) -> list[OutboxEvent]:
    with session_scope(admin) as s:
        return list(
            s.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.workflow_id == uuid.UUID(workflow_id),
                    OutboxEvent.event_type == outbox.WORKFLOW_ORCHESTRATE,
                )
                .order_by(OutboxEvent.created_at)
            )
        )


def _run(worker: Factory, event: OutboxEvent) -> job.Outcome:
    with session_scope(worker) as s:
        return job.run(
            s,
            event_id=event.id,
            workspace_id=event.workspace_id,
            workflow_id=uuid.UUID(event.payload["workflow_id"]),
        )


def _workflow(client: TestClient, workflow_id: str) -> dict[str, Any]:
    body: dict[str, Any] = client.get(_url(client, f"/{workflow_id}")).json()
    return body


def test_each_status_change_requests_one_assessment_that_is_stored_without_a_revision(
    owner: TestClient, admin_factory: Factory, worker_factory: Factory
) -> None:
    created = owner.post(_url(owner), json={"prompt": "Write a short report."}, headers=csrf(owner))
    workflow_id = created.json()["id"]
    assert created.json()["next_step"] is None  # not assessed yet
    assert _requests(admin_factory, workflow_id) == []  # creating changes no status

    _move(owner, workflow_id, "PLANNING")
    (request,) = _requests(admin_factory, workflow_id)  # one per transaction
    assert request.payload == {"workspace_id": my_workspace(owner), "workflow_id": workflow_id}
    before = _workflow(owner, workflow_id)["revision"]

    assert _run(worker_factory, request) == job.Outcome.ASSESSED
    assert _run(worker_factory, request) == job.Outcome.DUPLICATE  # redelivery

    body = _workflow(owner, workflow_id)
    assert body["revision"] == before
    step = body["next_step"]
    assert step["kind"] == "complete_plan" and step["actor"] == "people"
    assert step["graph_version"] == "orchestration/v1" and step["assessed_at"]
    assert step["task_ids"] == []


def test_assessments_follow_the_committed_state_in_any_delivery_order(
    owner: TestClient, admin_factory: Factory, worker_factory: Factory
) -> None:
    workflow_id = owner.post(
        _url(owner),
        json={"prompt": "Write a short report.", "settings": {"autonomy_policy": "autonomous"}},
        headers=csrf(owner),
    ).json()["id"]
    _move(owner, workflow_id, "PLANNING")
    task = owner.post(
        _url(owner, f"/{workflow_id}/tasks"), json={"title": "Draft"}, headers=csrf(owner)
    ).json()
    for status in ("READY", "RUNNING"):
        _move(owner, workflow_id, status)

    requests = _requests(admin_factory, workflow_id)
    assert len(requests) == 3  # PLANNING, READY, and RUNNING (which also readies the task)
    # The newest delivery first, then an old one: both read the state as committed now.
    assert _run(worker_factory, requests[-1]) == job.Outcome.ASSESSED
    newest = _workflow(owner, workflow_id)["next_step"]
    assert _run(worker_factory, requests[0]) == job.Outcome.ASSESSED
    oldest = _workflow(owner, workflow_id)["next_step"]

    for step in (newest, oldest):
        assert step["kind"] == "assign_agents" and step["task_ids"] == [task["id"]]
        assert step["summary"] == "Assign agents to 1 task."


def test_a_delivery_for_a_workflow_of_another_workspace_assesses_nothing(
    owner: TestClient, new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    workflow_id = owner.post(
        _url(owner), json={"prompt": "Write a short report."}, headers=csrf(owner)
    ).json()["id"]
    _move(owner, workflow_id, "PLANNING")
    (request,) = _requests(admin_factory, workflow_id)
    other = new_client()
    register(other, new_email())

    forged = OutboxEvent(
        id=uuid.uuid4(),
        workspace_id=uuid.UUID(my_workspace(other)),
        event_type=outbox.WORKFLOW_ORCHESTRATE,
        payload={"workspace_id": my_workspace(other), "workflow_id": workflow_id},
    )
    assert _run(worker_factory, forged) == job.Outcome.MISSING
    assert _workflow(owner, workflow_id)["next_step"] is None
    assert _run(worker_factory, request) == job.Outcome.ASSESSED
