"""Model gateway usage records in real PostgreSQL (ADR-0013), written as the worker role."""

import uuid
from collections.abc import Callable
from functools import partial
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.sync_session import session_scope
from app.llm.gateway import ModelCallFailedError, ModelGateway, ModelOutputInvalidError
from app.llm.mock import MockModelAdapter, MockProviderError
from app.llm.types import CallContext, ChatMessage, ModelRequest
from app.models import LlmCallStatus, LlmUsage
from app.services.usage import record_model_call
from tests.integration.api_helpers import (
    API,
    ClientFactory,
    csrf,
    my_workspace,
    new_email,
    register,
    sql,
)

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]


class Verdict(BaseModel):
    ok: bool


def _tenant(new_client: ClientFactory) -> dict[str, str]:
    """A user's workspace with one workflow and one task, created through the API."""
    client = new_client()
    assert register(client, new_email()).status_code == 201
    workspace = my_workspace(client)
    workflow = client.post(
        f"{API}/workspaces/{workspace}/workflows", json={"prompt": "goal"}, headers=csrf(client)
    ).json()["id"]
    task = client.post(
        f"{API}/workspaces/{workspace}/workflows/{workflow}/tasks",
        json={"title": "t"},
        headers=csrf(client),
    ).json()["id"]
    user = client.get(f"{API}/auth/me").json()["user"]["id"]
    return {"workspace_id": workspace, "workflow_id": workflow, "task_id": task, "user_id": user}


def _context(tenant: dict[str, str]) -> CallContext:
    return CallContext(
        workspace_id=uuid.UUID(tenant["workspace_id"]),
        workflow_id=uuid.UUID(tenant["workflow_id"]),
        task_id=uuid.UUID(tenant["task_id"]),
    )


def _request(purpose: str = "verdict") -> ModelRequest:
    return ModelRequest(purpose=purpose, messages=[ChatMessage(role="user", content="judge it")])


@pytest.fixture
def gateway_with(
    api_settings: Settings, worker_factory: Factory
) -> Callable[[MockModelAdapter], ModelGateway]:
    def build(adapter: MockModelAdapter) -> ModelGateway:
        return ModelGateway(api_settings, adapter, partial(record_model_call, worker_factory))

    return build


def _rows(admin_factory: Factory) -> list[LlmUsage]:
    with session_scope(admin_factory) as s:
        return list(s.scalars(select(LlmUsage).order_by(LlmUsage.created_at)))


def test_every_call_is_recorded_with_its_context_and_no_invented_cost(
    new_client: ClientFactory,
    gateway_with: Callable[[MockModelAdapter], ModelGateway],
    admin_factory: Factory,
) -> None:
    tenant = _tenant(new_client)
    adapter = MockModelAdapter(defaults={})
    adapter.script("verdict", '{"ok": true}', "not json", MockProviderError("boom"))
    gateway = gateway_with(adapter)

    response = gateway.complete(_context(tenant), _request(), Verdict)
    with pytest.raises(ModelOutputInvalidError):
        gateway.complete(_context(tenant), _request(), Verdict)
    with pytest.raises(ModelCallFailedError):
        gateway.complete(_context(tenant), _request(), Verdict)

    rows = _rows(admin_factory)
    assert [r.status for r in rows] == [
        LlmCallStatus.SUCCEEDED,
        LlmCallStatus.INVALID_OUTPUT,
        LlmCallStatus.FAILED,
    ]
    succeeded = rows[0]
    assert succeeded.id == response.usage_id
    assert (str(succeeded.workspace_id), str(succeeded.workflow_id), str(succeeded.task_id)) == (
        tenant["workspace_id"],
        tenant["workflow_id"],
        tenant["task_id"],
    )
    assert (succeeded.provider, succeeded.model, succeeded.purpose) == ("mock", "mock-1", "verdict")
    assert succeeded.input_tokens == 2 and succeeded.output_tokens == 2
    assert succeeded.provider_request_id == response.provider_request_id
    # Pricing arrives in Phase 7: nothing is estimated or labelled as an actual cost.
    for row in rows:
        assert (row.input_cost, row.output_cost, row.total_cost, row.currency) == (None,) * 4
        assert row.pricing_version is None
    assert rows[2].input_tokens is None and rows[2].error_code == "MODEL_CALL_FAILED"


def test_usage_survives_a_rollback_of_the_callers_work(
    new_client: ClientFactory,
    gateway_with: Callable[[MockModelAdapter], ModelGateway],
    worker_factory: Factory,
    admin_factory: Factory,
) -> None:
    tenant = _tenant(new_client)
    gateway = gateway_with(MockModelAdapter(defaults={}))

    with session_scope(worker_factory) as caller:
        caller.execute(text("SELECT 1"))  # the caller's own open transaction
        gateway.complete(_context(tenant), _request("note"))
        caller.rollback()

    assert len(_rows(admin_factory)) == 1


def test_usage_rows_are_isolated_per_workspace_by_row_level_security(
    new_client: ClientFactory,
    gateway_with: Callable[[MockModelAdapter], ModelGateway],
    api_settings: Settings,
) -> None:
    alice, bob = _tenant(new_client), _tenant(new_client)
    gateway = gateway_with(MockModelAdapter(defaults={}))
    gateway.complete(_context(alice), _request("note"))
    gateway.complete(_context(bob), _request("note"))

    engine = create_engine(api_settings.api_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.user_id', :u, true)"), {"u": alice["user_id"]}
            )
            seen = connection.execute(text("SELECT workspace_id FROM llm_usage")).scalars().all()
    finally:
        engine.dispose()
    assert [str(w) for w in seen] == [alice["workspace_id"]]


def test_workers_cannot_rewrite_usage_history(
    new_client: ClientFactory,
    gateway_with: Callable[[MockModelAdapter], ModelGateway],
    worker_factory: Factory,
) -> None:
    gateway_with(MockModelAdapter(defaults={})).complete(
        _context(_tenant(new_client)), _request("note")
    )

    for statement in ("UPDATE llm_usage SET output_tokens = 0", "DELETE FROM llm_usage"):
        with (
            pytest.raises(DBAPIError, match="permission denied"),
            session_scope(worker_factory) as s,
        ):
            s.execute(text(statement))


@pytest.mark.parametrize(
    "values",
    [
        # A cost without its currency and pricing version.
        {"input_cost": 1, "output_cost": 1, "total_cost": 2},
        # A failed call must carry an error code, a successful one must not.
        {"status": "FAILED"},
        {"error_code": "X"},
        {"input_tokens": -1},
        # A task always belongs to the row's workflow.
        {"workflow_id": None},
    ],
)
def test_the_database_rejects_inconsistent_usage_rows(
    new_client: ClientFactory, api_settings: Settings, values: dict[str, Any]
) -> None:
    tenant = _tenant(new_client)
    row: dict[str, Any] = {
        "workspace_id": tenant["workspace_id"],
        "workflow_id": tenant["workflow_id"],
        "task_id": tenant["task_id"],
        "purpose": "p",
        "provider": "mock",
        "model": "mock-1",
        "status": "SUCCEEDED",
        "latency_ms": 1,
        **values,
    }
    columns = ", ".join(row)
    params = ", ".join(f":{c}" for c in row)
    with pytest.raises(IntegrityError):
        sql(api_settings, f"INSERT INTO llm_usage ({columns}) VALUES ({params})", **row)  # noqa: S608


def test_a_litellm_call_is_recorded_with_provider_usage_and_no_cost(
    new_client: ClientFactory,
    api_settings: Settings,
    worker_factory: Factory,
    admin_factory: Factory,
) -> None:
    """Step 3.3 (ADR-0020): LiteLLM's real code path, offline through its `mock_response`."""
    import litellm

    from app.llm.litellm_adapter import LiteLLMAdapter

    tenant = _tenant(new_client)
    settings = api_settings.model_copy(
        update={
            "model_provider": "litellm",
            "model_name": "anthropic/claude-sonnet-5-5",
            "model_api_key": SecretStr("integration-test-model-key"),
        }
    )
    adapter = LiteLLMAdapter(settings, partial(litellm.completion, mock_response='{"ok": true}'))
    gateway = ModelGateway(settings, adapter, partial(record_model_call, worker_factory))

    response = gateway.complete(_context(tenant), _request(), Verdict)

    (row,) = _rows(admin_factory)
    assert row.id == response.usage_id and response.parsed == Verdict(ok=True)
    assert (row.provider, row.model) == ("anthropic", "anthropic/claude-sonnet-5-5")
    assert (row.input_tokens, row.output_tokens) == (10, 20)
    assert row.other_usage["total_tokens"] == 30 and row.other_usage["reported_via"] == "litellm"
    assert row.provider_request_id == response.provider_request_id
    assert (row.input_cost, row.output_cost, row.total_cost, row.currency) == (None,) * 4
    assert row.pricing_version is None
