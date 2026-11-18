"""Cost calculation, reconciliation and budgets (Step 7.2, ADR-0040) against real PostgreSQL.

Calls are recorded through the usage tracker as the worker records them (`worker_factory`).
"""

import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.agents.mock import MockAgent
from app.db.sync_session import session_scope
from app.execution import outbox, recovery, runner
from app.llm import pricing
from app.llm.gateway import ModelGateway
from app.llm.mock import MockModelAdapter
from app.llm.types import CallContext, ProviderUsage
from app.models import (
    Agent,
    AgentStatus,
    AgentVersion,
    AutonomyPolicy,
    CostBasis,
    LlmCallStatus,
    LlmUsage,
    OutboxEvent,
    PauseReason,
    Workflow,
    WorkflowStatus,
    WorkflowTask,
)
from app.services.usage import CallRecord, record_model_call
from app.workflows import engine
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


@pytest.fixture
def prices(admin_factory: Factory, tmp_path: Path) -> None:
    """acme reports its usage; estco does not (its counts are estimates). The mock model has a
    non-zero price here, so a test would notice if mock spend were ever counted as real."""
    content: dict[str, Any] = {
        "version": "costs-1",
        "currency": "USD",
        "effective_from": "2026-01-01T00:00:00Z",
        "source": "test",
        "providers": {
            "mock": {
                "reports_usage": True,
                "models": {
                    "mock-1": {
                        "input_per_million": "1000",
                        "output_per_million": "1000",
                        "mock": True,
                    }
                },
            },
            "acme": {
                "reports_usage": True,
                "models": {
                    "acme-large": {
                        "input_per_million": "1.5",
                        "output_per_million": "6",
                        "cached_input_per_million": "0.75",
                    }
                },
            },
            "estco": {
                "reports_usage": False,
                "models": {"est-1": {"input_per_million": "2", "output_per_million": "2"}},
            },
        },
    }
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    with session_scope(admin_factory) as s:
        pricing.load(s, path)
        s.commit()


def _owner(new_client: ClientFactory) -> TestClient:
    client = new_client()
    assert register(client, new_email()).status_code == 201
    return client


def _running(
    admin: Factory,
    client: TestClient,
    *,
    budget: str | None = None,
    currency: str = "USD",
    assigned: bool = False,
) -> tuple[Workflow, WorkflowTask]:
    """A RUNNING autonomous workflow in the client's workspace with one task."""
    workspace_id = uuid.UUID(my_workspace(client))
    user_id = uuid.UUID(client.get(f"{API}/auth/me").json()["user"]["id"])
    with session_scope(admin) as s:
        agent_id = version_id = None
        if assigned:
            agent = Agent(workspace_id=workspace_id, name=f"W {uuid.uuid4().hex[:6]}", category="w")
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
            agent_id, version_id = agent.id, version.id
        workflow = Workflow(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            prompt="p",
            autonomy_policy=AutonomyPolicy.AUTONOMOUS,
            budget_limit=Decimal(budget) if budget is not None else None,
            currency=currency,
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
            assigned_agent_id=agent_id,
            assigned_agent_version_id=version_id,
        )
        s.add(task)
        s.flush()
        for target in (WorkflowStatus.PLANNING, WorkflowStatus.READY, WorkflowStatus.RUNNING):
            engine.transition_workflow(s, workflow, target, actor=Actor.USER)
        s.commit()
        return workflow, task


def _call(
    worker: Factory,
    workflow: Workflow,
    provider: str,
    model: str,
    usage: ProviderUsage | None,
    *,
    status: LlmCallStatus = LlmCallStatus.SUCCEEDED,
    execution_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> uuid.UUID:
    return record_model_call(
        worker,
        CallRecord(
            context=CallContext(
                workspace_id=workflow.workspace_id,
                workflow_id=workflow.id,
                task_id=task_id,
                execution_id=execution_id,
            ),
            purpose="test",
            provider=provider,
            model=model,
            status=status,
            usage=usage,
            provider_request_id=None,
            error_code=None if status == LlmCallStatus.SUCCEEDED else "MODEL_CALL_FAILED",
            latency_ms=1,
        ),
    )


def _row(admin: Factory, usage_id: uuid.UUID) -> LlmUsage:
    with session_scope(admin) as s:
        return s.get_one(LlmUsage, usage_id)


def _workflow(admin: Factory, workflow_id: uuid.UUID) -> Workflow:
    with session_scope(admin) as s:
        return s.get_one(Workflow, workflow_id)


def _costs(client: TestClient, workflow: Workflow) -> dict[str, Any]:
    response = client.get(f"{API}/workspaces/{workflow.workspace_id}/workflows/{workflow.id}/costs")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


ACME = ProviderUsage(input_tokens=1000, output_tokens=500, cached_input_tokens=400)


@pytest.mark.usefixtures("prices")
def test_each_call_is_costed_from_its_price_with_its_basis(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    workflow, _ = _running(admin_factory, _owner(new_client))

    actual = _row(admin_factory, _call(worker_factory, workflow, "acme", "acme-large", ACME))
    # 600 billable at 1.5 + 400 cached at 0.75, and 500 output at 6, per million.
    assert (actual.input_cost, actual.output_cost, actual.total_cost) == (
        Decimal("0.0012"),
        Decimal("0.003"),
        Decimal("0.0042"),
    )
    assert (actual.currency, actual.pricing_version, actual.cost_basis) == (
        "USD",
        "costs-1",
        CostBasis.ACTUAL,
    )
    estimated = _row(
        admin_factory,
        _call(worker_factory, workflow, "estco", "est-1", ProviderUsage(1000, 1000)),
    )
    assert estimated.cost_basis == CostBasis.ESTIMATED and estimated.total_cost == Decimal("0.004")
    mock = _row(admin_factory, _call(worker_factory, workflow, "mock", "mock-1", ACME))
    # 1000 input (no cached price: all at the input price) and 500 output, at 1000 per million.
    assert mock.cost_basis == CostBasis.MOCK and mock.total_cost == Decimal("1.5")
    # Unpriced: no price for the model, or no counts. A failed call with reported usage is billed.
    for provider, model, usage in (("acme", "unknown", ACME), ("acme", "acme-large", None)):
        unpriced = _row(admin_factory, _call(worker_factory, workflow, provider, model, usage))
        assert (unpriced.total_cost, unpriced.cost_basis, unpriced.currency) == (None, None, None)
    failed = _row(
        admin_factory,
        _call(worker_factory, workflow, "acme", "acme-large", ACME, status=LlmCallStatus.FAILED),
    )
    assert failed.cost_basis == CostBasis.ACTUAL and failed.total_cost == Decimal("0.0042")


@pytest.mark.usefixtures("prices")
def test_costs_are_totalled_by_basis_and_reconcile_with_their_prices(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    owner = _owner(new_client)
    workflow, _ = _running(admin_factory, owner, budget="1")
    tampered = _call(worker_factory, workflow, "acme", "acme-large", ACME)
    _call(worker_factory, workflow, "acme", "acme-large", ACME, status=LlmCallStatus.INVALID_OUTPUT)
    _call(worker_factory, workflow, "estco", "est-1", ProviderUsage(1000, 1000))
    _call(worker_factory, workflow, "mock", "mock-1", ACME)
    _call(worker_factory, workflow, "acme", "acme-large", None)

    viewer = member(new_client, owner, "VIEWER")
    body = _costs(viewer, workflow)
    assert body["calls"] == 5 and body["unpriced_calls"] == 1
    assert body["calls_by_status"] == {"SUCCEEDED": 4, "INVALID_OUTPUT": 1}
    assert (body["input_tokens"], body["output_tokens"]) == (4000, 2500)
    (usd,) = body["totals"]
    assert (usd["currency"], Decimal(usd["actual"]), Decimal(usd["estimated"])) == (
        "USD",
        Decimal("0.0084"),
        Decimal("0.004"),
    )
    assert Decimal(usd["mock"]) == Decimal("1.5")  # shown apart, never counted
    assert body["budget"] == {
        "limit": "1.0000",
        "currency": "USD",
        "comparable": True,
        "counted_spend": "0.01240000",
        "reached": False,
    }
    assert (body["checked_calls"], body["mismatched_calls"], body["reconciled"]) == (4, 0, True)

    # A stored cost that no longer matches its price snapshot is reported, never hidden.
    with session_scope(admin_factory) as s:
        s.execute(text("UPDATE llm_usage SET total_cost = 0 WHERE id = :id"), {"id": tampered})
        s.commit()
    changed = _costs(owner, workflow)
    assert (changed["mismatched_calls"], changed["reconciled"]) == (1, False)

    mallory = _owner(new_client)
    foreign = mallory.get(f"{API}/workspaces/{workflow.workspace_id}/workflows/{workflow.id}/costs")
    assert foreign.status_code == 404


@pytest.mark.usefixtures("prices")
def test_reaching_the_budget_pauses_the_workflow_once(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    # Handoff critical scenario 13: a budget cap prevents further dispatch.
    owner = _owner(new_client)
    workflow, _ = _running(admin_factory, owner, budget="0.005")
    _call(worker_factory, workflow, "mock", "mock-1", ACME)  # 1.5, far over: never counts
    _call(worker_factory, workflow, "acme", "acme-large", ACME)  # 0.0042: under the budget
    assert recovery.recover_once(worker_factory).paused_at_budget == 0

    _call(worker_factory, workflow, "estco", "est-1", ProviderUsage(1000, 1000))  # estimates count
    assert recovery.recover_once(worker_factory).paused_at_budget == 1
    paused = _workflow(admin_factory, workflow.id)
    assert (
        paused.status == WorkflowStatus.PAUSED and paused.pause_reason == PauseReason.BUDGET_REACHED
    )
    assert _costs(owner, workflow)["budget"]["reached"] is True

    # Resuming past the budget is a person's choice: it does not pause again.
    with session_scope(admin_factory) as s:
        engine.transition_workflow(
            s, s.get_one(Workflow, workflow.id), WorkflowStatus.RUNNING, actor=Actor.USER
        )
        s.commit()
    _call(worker_factory, workflow, "acme", "acme-large", ACME)
    assert recovery.recover_once(worker_factory).paused_at_budget == 0
    assert _workflow(admin_factory, workflow.id).status == WorkflowStatus.RUNNING


@pytest.mark.usefixtures("prices")
def test_a_budget_in_another_currency_is_not_comparable_and_not_enforced(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    owner = _owner(new_client)
    workflow, _ = _running(admin_factory, owner, budget="0.001", currency="EUR")
    _call(worker_factory, workflow, "acme", "acme-large", ACME)

    assert recovery.recover_once(worker_factory).paused_at_budget == 0
    assert _workflow(admin_factory, workflow.id).status == WorkflowStatus.RUNNING
    budget = _costs(owner, workflow)["budget"]
    assert (budget["comparable"], budget["reached"], budget["counted_spend"]) == (False, False, "0")


@pytest.mark.usefixtures("prices")
def test_the_runner_stops_at_the_budget_after_an_attempt(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    workflow, task = _running(admin_factory, _owner(new_client), budget="0.001", assigned=True)
    with session_scope(admin_factory) as s:
        event = s.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_type == outbox.TASK_DISPATCH, OutboxEvent.task_id == task.id
            )
        ).one()
    with session_scope(worker_factory) as s:
        claim = runner.claim(
            s,
            event_id=event.id,
            workspace_id=workflow.workspace_id,
            workflow_id=workflow.id,
            task_id=task.id,
            agent_version_id=uuid.UUID(event.payload["agent_version_id"]),
        )
    assert isinstance(claim, runner.Claim)
    # The attempt's model call, recorded by the gateway before the attempt finishes.
    _call(
        worker_factory,
        workflow,
        "acme",
        "acme-large",
        ACME,
        execution_id=claim.execution_id,
        task_id=task.id,
    )
    outcome = runner.run_agent(claim, MockAgent, models=_NO_MODEL, is_current=lambda: True)
    with session_scope(worker_factory) as s:
        assert runner.finish(s, claim, outcome) == "submitted"

    paused = _workflow(admin_factory, workflow.id)
    assert (
        paused.status == WorkflowStatus.PAUSED and paused.pause_reason == PauseReason.BUDGET_REACHED
    )
