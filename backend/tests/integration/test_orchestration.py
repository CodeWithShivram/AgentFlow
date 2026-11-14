"""The orchestration job in PostgreSQL (Step 4.1, ADR-0025), run as the restricted worker role."""

import uuid
from typing import Any, cast

import psycopg
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.sync_session import session_scope
from app.execution import cleanup, outbox
from app.models import (
    Checkpoint,
    CheckpointBlob,
    CheckpointWrite,
    OutboxEvent,
    Workflow,
)
from app.models.checkpoint import CHECKPOINT_SCHEMA_VERSIONS
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
    assert step["graph_version"] == "orchestration/v2" and step["assessed_at"]
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


# --- checkpoints (Step 4.2, ADR-0026) ------------------------------------------------------------


def _delivery(workspace_id: str, workflow_id: str) -> OutboxEvent:
    """A fresh delivery of a reassessment request (only its id and payload are used)."""
    return OutboxEvent(
        id=uuid.uuid4(),
        workspace_id=uuid.UUID(workspace_id),
        event_type=outbox.WORKFLOW_ORCHESTRATE,
        payload={"workspace_id": workspace_id, "workflow_id": workflow_id},
    )


def _checkpoint_ids(admin: Factory, thread_id: str) -> list[str]:
    with session_scope(admin) as s:
        return list(
            s.scalars(
                select(Checkpoint.checkpoint_id)
                .where(Checkpoint.thread_id == thread_id)
                .order_by(Checkpoint.checkpoint_id)
            )
        )


def test_every_assessment_is_checkpointed_per_workflow_and_only_workers_can_read_them(
    owner: TestClient, admin_factory: Factory, worker_factory: Factory, api_settings: Settings
) -> None:
    prompt = "Summarise the confidential board minutes."
    workflow_id = owner.post(_url(owner), json={"prompt": prompt}, headers=csrf(owner)).json()["id"]
    workspace_id = my_workspace(owner)
    _move(owner, workflow_id, "PLANNING")

    for _ in range(2):
        assert _run(worker_factory, _delivery(workspace_id, workflow_id)) == job.Outcome.ASSESSED

    checkpoints = _checkpoint_ids(admin_factory, workflow_id)
    assert len(checkpoints) >= 2  # one thread per workflow, a checkpoint per run (and step)
    with session_scope(admin_factory) as s:
        workflow = s.get(Workflow, uuid.UUID(workflow_id))
        assert workflow is not None and workflow.next_step is not None
        assert workflow.next_step["checkpoint_id"] == checkpoints[-1]
        stored = [
            str(row.checkpoint) + str(row.metadata_)
            for row in s.scalars(select(Checkpoint).where(Checkpoint.thread_id == workflow_id))
        ]
        stored += [
            (blob.blob or b"").decode("latin-1")
            for blob in s.scalars(
                select(CheckpointBlob).where(CheckpointBlob.thread_id == workflow_id)
            )
        ]
    assert stored and not any("board minutes" in text for text in stored)  # no prompt text

    # The API role has no access at all, and the library's own setup() would change nothing.
    engine = create_engine(api_settings.api_database_url)
    try:
        with engine.connect() as connection, pytest.raises(ProgrammingError):
            connection.execute(text("SELECT count(*) FROM checkpoints"))
    finally:
        engine.dispose()
    with psycopg.connect(
        api_settings.database_url.render_as_string(hide_password=False).replace("+psycopg", ""),
        autocommit=True,
    ) as connection:
        PostgresSaver(connection).setup()  # type: ignore[arg-type]
        versions = connection.execute("SELECT count(*) FROM checkpoint_migrations").fetchone()
    assert versions == (CHECKPOINT_SCHEMA_VERSIONS,)


def test_cleanup_keeps_the_newest_checkpoints_per_workflow_and_drops_orphans(
    owner: TestClient, admin_factory: Factory, worker_factory: Factory
) -> None:
    workflow_id = owner.post(
        _url(owner), json={"prompt": "Write a short report."}, headers=csrf(owner)
    ).json()["id"]
    workspace_id = my_workspace(owner)
    _move(owner, workflow_id, "PLANNING")
    for _ in range(12):
        assert _run(worker_factory, _delivery(workspace_id, workflow_id)) == job.Outcome.ASSESSED
    before = _checkpoint_ids(admin_factory, workflow_id)
    assert len(before) > cleanup.CHECKPOINTS_KEPT_PER_WORKFLOW
    orphan = str(uuid.uuid4())  # a thread whose workflow no longer exists
    with session_scope(admin_factory) as s:
        s.add(Checkpoint(thread_id=orphan, checkpoint_id="1", checkpoint={}, metadata_={}))
        s.add(CheckpointBlob(thread_id=orphan, channel="tasks", version="1", type="empty"))
        s.add(
            CheckpointWrite(
                thread_id=orphan, checkpoint_id="1", task_id="t", idx=0, channel="c", blob=b""
            )
        )
        s.commit()

    counts = cleanup.run_cleanup(worker_factory, batch_size=5, max_batches=20)

    after = _checkpoint_ids(admin_factory, workflow_id)
    assert after == before[-cleanup.CHECKPOINTS_KEPT_PER_WORKFLOW :]  # the newest are kept
    assert _checkpoint_ids(admin_factory, orphan) == []
    assert counts["checkpoints"] == len(before) - len(after) + 1
    assert counts["checkpoint_writes"] >= 1 and counts["checkpoint_blobs"] >= 1
    with session_scope(admin_factory) as s:
        referenced = s.scalars(select(Checkpoint).where(Checkpoint.thread_id == workflow_id))
        versions = {
            (channel, version)
            for row in referenced
            for channel, version in cast(dict[str, str], row.checkpoint["channel_versions"]).items()
        }
        blobs = {
            (b.channel, b.version)
            for b in s.scalars(
                select(CheckpointBlob).where(CheckpointBlob.thread_id == workflow_id)
            )
        }
    assert blobs and blobs <= versions  # every remaining blob is still referenced
    # The thread continues from its newest checkpoint, and cleanup trims it back to the limit.
    assert _run(worker_factory, _delivery(workspace_id, workflow_id)) == job.Outcome.ASSESSED
    cleanup.run_cleanup(worker_factory, batch_size=5, max_batches=20)
    assert len(_checkpoint_ids(admin_factory, workflow_id)) == cleanup.CHECKPOINTS_KEPT_PER_WORKFLOW
