"""Failed background jobs, permanent publish failures and the relay heartbeat (ADR-0027)."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.sync_session import session_scope
from app.execution import heartbeat, outbox, relay
from app.models import OutboxEvent, OutboxStatus
from app.worker import tasks
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
SECRET_HOST = "10.0.0.5:6379"


@pytest.fixture
def owner(new_client: ClientFactory) -> TestClient:
    client = new_client()
    assert register(client, new_email()).status_code == 201
    return client


def _workflow(client: TestClient) -> str:
    response = client.post(
        f"{API}/workspaces/{my_workspace(client)}/workflows",
        json={"prompt": "Write a report."},
        headers=csrf(client),
    )
    workflow_id: str = response.json()["id"]
    return workflow_id


def _failed_event(admin: Factory, workspace_id: str, workflow_id: str, **fields: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "workspace_id": uuid.UUID(workspace_id),
        "workflow_id": uuid.UUID(workflow_id),
        "event_type": outbox.WORKFLOW_ORCHESTRATE,
        "payload": {"workspace_id": workspace_id, "workflow_id": workflow_id},
        "status": OutboxStatus.FAILED,
        "attempts": 10,
        "last_error": f"ConnectionError: Error 10061 connecting to {SECRET_HOST}.",
    }
    values.update(fields)
    with session_scope(admin) as s:
        s.add(OutboxEvent(**values))
        s.commit()
    event_id: uuid.UUID = values["id"]
    return event_id


def _url(client: TestClient, workflow_id: str, suffix: str = "") -> str:
    return f"{API}/workspaces/{my_workspace(client)}/workflows/{workflow_id}/failed-jobs{suffix}"


def test_failed_jobs_are_visible_to_members_and_retryable_by_admins(
    owner: TestClient, new_client: ClientFactory, admin_factory: Factory
) -> None:
    workspace_id = my_workspace(owner)
    workflow_id = _workflow(owner)
    event_id = _failed_event(admin_factory, workspace_id, workflow_id)
    viewer = member(new_client, owner, "VIEWER")
    editor = member(new_client, owner, "MEMBER")

    listed = viewer.get(f"{API}/workspaces/{workspace_id}/workflows/{workflow_id}/failed-jobs")
    assert listed.status_code == 200
    (job,) = listed.json()["items"]
    assert job["id"] == str(event_id) and job["event_type"] == "workflow.orchestrate"
    assert job["attempts"] == 10 and job["error_type"] == "ConnectionError"
    assert SECRET_HOST not in listed.text  # never the stored error text

    retry_url = (
        f"{API}/workspaces/{workspace_id}/workflows/{workflow_id}/failed-jobs/{event_id}/retry"
    )
    assert editor.post(retry_url, headers=csrf(editor)).status_code == 403
    retried = owner.post(retry_url, headers=csrf(owner))
    assert retried.status_code == 200, retried.text
    with session_scope(admin_factory) as s:
        event = s.get(OutboxEvent, event_id)
        assert event is not None
        assert (event.status, event.attempts, event.lease_owner) == (OutboxStatus.PENDING, 0, None)
    timeline = owner.get(f"{API}/workspaces/{workspace_id}/workflows/{workflow_id}/events").json()
    (audit,) = [e for e in timeline["items"] if e["event_type"] == "BACKGROUND_JOB_RETRIED"]
    assert audit["payload"]["event_id"] == str(event_id)
    assert audit["payload"]["previous_attempts"] == 10
    again = owner.post(retry_url, headers=csrf(owner))
    assert again.status_code == 404 and again.json()["error"]["code"] == "FAILED_JOB_NOT_FOUND"
    assert viewer.get(_url(viewer, workflow_id)).json()["items"] == []


def test_the_workspace_listing_is_for_admins_and_scoped(
    owner: TestClient, new_client: ClientFactory, admin_factory: Factory
) -> None:
    workspace_id = my_workspace(owner)
    workflow_id = _workflow(owner)
    _failed_event(admin_factory, workspace_id, workflow_id)
    editor = member(new_client, owner, "MEMBER")
    mallory = new_client()
    register(mallory, new_email())

    page = owner.get(f"{API}/workspaces/{workspace_id}/failed-jobs").json()
    assert page["total"] == 1 and page["items"][0]["workflow_id"] == workflow_id
    assert editor.get(f"{API}/workspaces/{workspace_id}/failed-jobs").status_code == 403
    assert mallory.get(f"{API}/workspaces/{workspace_id}/failed-jobs").status_code == 404
    theirs = my_workspace(mallory)
    assert mallory.get(f"{API}/workspaces/{theirs}/failed-jobs").json()["total"] == 0
    assert (
        mallory.get(f"{API}/workspaces/{theirs}/workflows/{workflow_id}/failed-jobs").status_code
        == 404
    )


@pytest.mark.parametrize(
    ("event_type", "payload", "reason"),
    [
        ("unknown.event", {"workspace_id": "w", "workflow_id": "f"}, "no route"),
        (outbox.TASK_DISPATCH, {"workspace_id": "w", "workflow_id": "f"}, "payload lacks"),
    ],
)
def test_events_that_can_never_be_published_fail_at_once(
    owner: TestClient,
    admin_factory: Factory,
    worker_factory: Factory,
    event_type: str,
    payload: dict[str, str],
    reason: str,
) -> None:
    workspace_id = my_workspace(owner)
    event_id = uuid.uuid4()
    with session_scope(admin_factory) as s:
        s.add(
            OutboxEvent(
                id=event_id,
                workspace_id=uuid.UUID(workspace_id),
                event_type=event_type,
                payload=payload,
                status=OutboxStatus.PENDING,
                attempts=0,
            )
        )
        s.commit()

    report = relay.relay_once(
        worker_factory, tasks.publish_event, batch_size=10, lease_seconds=60, max_attempts=10
    )

    assert report.failed == 1
    with session_scope(admin_factory) as s:
        event = s.get(OutboxEvent, event_id)
        assert event is not None and event.status == OutboxStatus.FAILED
        assert event.attempts == 1  # no retries with backoff
        assert event.last_error is not None
        assert event.last_error.startswith("PermanentPublishError") and reason in event.last_error


def test_each_relay_run_records_a_heartbeat_with_the_backlog(
    owner: TestClient,
    admin_factory: Factory,
    worker_factory: Factory,
    api_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = redis.Redis(
        host=api_settings.redis_host,
        port=api_settings.redis_port,
        db=api_settings.redis_db,
        password=api_settings.redis_password.get_secret_value(),
    )
    monkeypatch.setattr(tasks, "_heartbeat_client", lambda: client)
    try:
        tasks._record_heartbeat(worker_factory)
        assert heartbeat.status(client.get(heartbeat.KEY)) == "ok"
        assert 0 < (client.ttl(heartbeat.KEY) or 0) <= heartbeat.TTL_SECONDS

        workflow_id = _workflow(owner)
        with session_scope(admin_factory) as s:
            s.add(
                OutboxEvent(
                    id=uuid.uuid4(),
                    workspace_id=uuid.UUID(my_workspace(owner)),
                    workflow_id=uuid.UUID(workflow_id),
                    event_type=outbox.WORKFLOW_ORCHESTRATE,
                    payload={},
                    status=OutboxStatus.PENDING,
                    attempts=0,
                    available_at=datetime.now(UTC) - timedelta(minutes=10),
                )
            )
            s.commit()
        tasks._record_heartbeat(worker_factory)
        record = json.loads(client.get(heartbeat.KEY) or "{}")
        assert record["oldest_due_seconds"] >= 600
        assert heartbeat.status(client.get(heartbeat.KEY)) == "lagging"
        with session_scope(admin_factory) as s:
            due = s.scalars(select(OutboxEvent).where(OutboxEvent.status == OutboxStatus.PENDING))
            assert len(list(due)) >= 1
    finally:
        client.delete(heartbeat.KEY)
        client.close()
