"""Prompt analysis (Step 2.2) end to end in PostgreSQL: API trigger, worker job, retry."""

import json
import uuid
from collections.abc import Callable
from functools import partial
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.sync_session import session_scope
from app.execution import outbox
from app.llm.gateway import ModelGateway
from app.llm.mock import MockModelAdapter
from app.models import LlmCallStatus, LlmUsage, OutboxEvent
from app.planning import analysis
from app.services.usage import record_model_call
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
PROMPT = "Research three CRM vendors. Write a short report with a pricing table."


@pytest.fixture
def owner(new_client: ClientFactory) -> TestClient:
    client = new_client()
    assert register(client, new_email()).status_code == 201
    return client


def _url(client: TestClient, workflow_id: str = "", suffix: str = "") -> str:
    base = f"{API}/workspaces/{my_workspace(client)}/workflows"
    return f"{base}/{workflow_id}{suffix}" if workflow_id else base


def _planning_workflow(client: TestClient, prompt: str = PROMPT) -> dict[str, Any]:
    created = client.post(_url(client), json={"prompt": prompt}, headers=csrf(client)).json()
    moved = client.post(
        _url(client, created["id"], "/transitions"),
        json={"to_status": "PLANNING", "revision": created["revision"]},
        headers=csrf(client),
    )
    assert moved.status_code == 200
    body: dict[str, Any] = moved.json()
    return body


def _requests(admin: Factory, workflow_id: str) -> list[OutboxEvent]:
    with session_scope(admin) as s:
        return list(
            s.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.workflow_id == uuid.UUID(workflow_id),
                    OutboxEvent.event_type == outbox.WORKFLOW_ANALYZE,
                )
                .order_by(OutboxEvent.created_at)
            )
        )


@pytest.fixture
def run_job(api_settings: Settings, worker_factory: Factory) -> Callable[..., str]:
    """Runs the worker job for one outbox event, as the restricted worker role."""

    def run(event: OutboxEvent, adapter: MockModelAdapter | None = None) -> str:
        gateway = ModelGateway(
            api_settings, adapter or MockModelAdapter(), partial(record_model_call, worker_factory)
        )
        assert event.workflow_id is not None
        with session_scope(worker_factory) as s:
            job = analysis.claim(
                s, event_id=event.id, workspace_id=event.workspace_id, workflow_id=event.workflow_id
            )
        if isinstance(job, analysis.Skipped):
            return job.value
        outcome = analysis.analyse(gateway, job, sleep=lambda _: None)
        with session_scope(worker_factory) as s:
            return analysis.finish(s, job, outcome)

    return run


def _events(client: TestClient, workflow_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = client.get(_url(client, workflow_id, "/events")).json()["items"]
    return items


def test_starting_to_plan_requests_an_analysis_in_the_same_transaction(
    owner: TestClient, admin_factory: Factory
) -> None:
    workflow = _planning_workflow(owner)

    assert workflow["analysis_status"] == "PENDING"
    assert workflow["normalized_requirements"] is None
    (request,) = _requests(admin_factory, workflow["id"])
    # Identifiers only: the prompt never travels through the broker.
    assert request.payload == {
        "workspace_id": str(request.workspace_id),
        "workflow_id": workflow["id"],
    }
    assert _events(owner, workflow["id"])[-1]["event_type"] == "ANALYSIS_REQUESTED"


def test_the_worker_stores_a_validated_analysis(
    owner: TestClient, admin_factory: Factory, run_job: Callable[..., str]
) -> None:
    workflow = _planning_workflow(owner)
    (request,) = _requests(admin_factory, workflow["id"])

    assert run_job(request) == "ready"

    stored = owner.get(_url(owner, workflow["id"])).json()
    assert stored["analysis_status"] == "READY"
    parsed = analysis.PromptAnalysis.model_validate(stored["normalized_requirements"])
    assert parsed.objective == "Research three CRM vendors."
    # Analysis never touches the plan, so the user's revision stays valid.
    assert stored["revision"] == workflow["revision"]
    event = _events(owner, workflow["id"])[-1]
    assert event["event_type"] == "REQUIREMENTS_ANALYZED"
    assert event["payload"]["prompt_version"] == analysis.PROMPT_VERSION
    assert PROMPT not in json.dumps(event["payload"])
    with session_scope(admin_factory) as s:
        usage = s.scalars(select(LlmUsage)).one()
    assert (str(usage.workflow_id), usage.purpose, usage.status) == (
        workflow["id"],
        analysis.PURPOSE,
        LlmCallStatus.SUCCEEDED,
    )


def test_failure_is_recorded_and_a_retry_supersedes_the_old_request(
    owner: TestClient, admin_factory: Factory, run_job: Callable[..., str]
) -> None:
    workflow = _planning_workflow(owner)
    (first,) = _requests(admin_factory, workflow["id"])
    broken = MockModelAdapter(defaults={})
    broken.script(analysis.PURPOSE, "not json", "{}", '{"objective": ""}')

    assert run_job(first, broken) == "failed"
    failed = owner.get(_url(owner, workflow["id"])).json()
    assert failed["analysis_status"] == "FAILED"
    event = _events(owner, workflow["id"])[-1]
    assert event["event_type"] == "ANALYSIS_FAILED"
    assert event["payload"]["error_code"] == "MODEL_OUTPUT_INVALID"
    with session_scope(admin_factory) as s:
        statuses = list(s.scalars(select(LlmUsage.status)))
    assert statuses == [LlmCallStatus.INVALID_OUTPUT] * analysis.MAX_ATTEMPTS

    retried = owner.post(_url(owner, workflow["id"], "/analysis"), headers=csrf(owner))
    assert retried.status_code == 200
    assert retried.json()["analysis_status"] == "PENDING"
    assert retried.json()["revision"] == workflow["revision"]
    old, new = _requests(admin_factory, workflow["id"])
    assert old.id == first.id

    assert run_job(old) == "duplicate"  # already consumed
    assert run_job(new) == "ready"


def test_a_stale_delivery_changes_nothing(
    owner: TestClient, admin_factory: Factory, run_job: Callable[..., str]
) -> None:
    workflow = _planning_workflow(owner)
    (first,) = _requests(admin_factory, workflow["id"])
    broken = MockModelAdapter(defaults={})
    broken.script(analysis.PURPOSE, "x", "x", "x")
    run_job(first, broken)
    owner.post(_url(owner, workflow["id"], "/analysis"), headers=csrf(owner))

    # A copy of the first request, redelivered after the retry (new event id, old request).
    with session_scope(admin_factory) as s:
        copy_id = outbox.enqueue(
            s,
            outbox.WORKFLOW_ANALYZE,
            workspace_id=first.workspace_id,
            workflow_id=first.workflow_id,
            payload=first.payload,
        )
        s.commit()
        copy = s.get(OutboxEvent, copy_id)
    assert copy is not None
    assert run_job(copy) == "stale"
    assert owner.get(_url(owner, workflow["id"])).json()["analysis_status"] == "PENDING"


@pytest.mark.parametrize("state", ["pending", "ready"])
def test_retry_is_refused_unless_the_analysis_failed(
    owner: TestClient, admin_factory: Factory, run_job: Callable[..., str], state: str
) -> None:
    workflow = _planning_workflow(owner)
    if state == "ready":
        run_job(_requests(admin_factory, workflow["id"])[0])

    response = owner.post(_url(owner, workflow["id"], "/analysis"), headers=csrf(owner))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ANALYSIS_NOT_RETRYABLE"


def test_viewers_cannot_request_an_analysis(
    owner: TestClient,
    new_client: ClientFactory,
    admin_factory: Factory,
    run_job: Callable[..., str],
) -> None:
    workflow = _planning_workflow(owner)
    broken = MockModelAdapter(defaults={})
    broken.script(analysis.PURPOSE, "x", "x", "x")
    run_job(_requests(admin_factory, workflow["id"])[0], broken)
    viewer = member(new_client, owner, "VIEWER")

    response = viewer.post(
        f"{API}/workspaces/{my_workspace(owner)}/workflows/{workflow['id']}/analysis",
        headers=csrf(viewer),
    )

    assert response.status_code == 403
