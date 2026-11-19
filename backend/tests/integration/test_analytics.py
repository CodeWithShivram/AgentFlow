"""Usage and cost analytics (Step 7.3, ADR-0041) against real PostgreSQL.

Usage is recorded through the usage tracker as workers record it, then read through the API.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.sync_session import session_scope
from app.llm import pricing
from app.llm.types import CallContext, ProviderUsage
from app.models import (
    Agent,
    AgentExecution,
    AgentStatus,
    AgentVersion,
    ExecutionStatus,
    LlmCallStatus,
    Workflow,
    WorkflowTask,
)
from app.services.usage import CallRecord, record_model_call
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
ACME = ProviderUsage(input_tokens=1000, output_tokens=500)  # 0.0015 + 0.003 = 0.0045 actual
EST = ProviderUsage(input_tokens=1000, output_tokens=1000)  # 0.002 + 0.002 = 0.004 estimated


@pytest.fixture
def prices(admin_factory: Factory, tmp_path: Path) -> None:
    content: dict[str, Any] = {
        "version": "analytics-1",
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
                "models": {"acme-large": {"input_per_million": "1.5", "output_per_million": "6"}},
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


@dataclass(frozen=True)
class Tenant:
    client: TestClient
    workspace_id: uuid.UUID
    writer: uuid.UUID
    researcher: uuid.UUID
    report: uuid.UUID  # workflow "Write the report", task "Draft"
    draft: uuid.UUID
    draft_by_writer: uuid.UUID  # attempt ids
    draft_by_researcher: uuid.UUID
    survey: uuid.UUID  # workflow "Run the survey", task "Collect"


def _tenant(new_client: ClientFactory, admin: Factory) -> Tenant:
    client = new_client()
    assert register(client, new_email()).status_code == 201
    workspace_id = uuid.UUID(my_workspace(client))
    user_id = uuid.UUID(client.get(f"{API}/auth/me").json()["user"]["id"])
    with session_scope(admin) as s:
        agents = {}
        for name in ("Writer", "Researcher"):
            agent = Agent(workspace_id=workspace_id, name=name, category="writing")
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
            agents[name] = (agent.id, version.id)
        workflows = {}
        for prompt, title in (("Write the report", "Draft"), ("Run the survey", "Collect")):
            workflow = Workflow(
                workspace_id=workspace_id, created_by_user_id=user_id, prompt=prompt
            )
            s.add(workflow)
            s.flush()
            task = WorkflowTask(
                workspace_id=workspace_id, workflow_id=workflow.id, title=title, description="d"
            )
            s.add(task)
            s.flush()
            workflows[prompt] = (workflow.id, task.id)
        report, draft = workflows["Write the report"]
        attempts = []
        for number, name in enumerate(("Writer", "Researcher"), start=1):
            execution = AgentExecution(
                workspace_id=workspace_id,
                workflow_id=report,
                task_id=draft,
                agent_id=agents[name][0],
                agent_version_id=agents[name][1],
                attempt_number=number,
                status=ExecutionStatus.FAILED,
                completed_at=datetime.now(UTC),
                dispatch_event_id=uuid.uuid4(),
            )
            s.add(execution)
            s.flush()
            attempts.append(execution.id)
        s.commit()
    return Tenant(
        client=client,
        workspace_id=workspace_id,
        writer=agents["Writer"][0],
        researcher=agents["Researcher"][0],
        report=report,
        draft=draft,
        draft_by_writer=attempts[0],
        draft_by_researcher=attempts[1],
        survey=workflows["Run the survey"][0],
    )


def _call(
    worker: Factory,
    tenant: Tenant,
    provider: str,
    model: str,
    usage: ProviderUsage | None,
    *,
    workflow_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    execution_id: uuid.UUID | None = None,
    purpose: str = "test",
) -> uuid.UUID:
    return record_model_call(
        worker,
        CallRecord(
            context=CallContext(
                workspace_id=tenant.workspace_id,
                workflow_id=workflow_id,
                task_id=task_id,
                execution_id=execution_id,
            ),
            purpose=purpose,
            provider=provider,
            model=model,
            status=LlmCallStatus.SUCCEEDED,
            usage=usage,
            provider_request_id=None,
            error_code=None,
            latency_ms=1,
        ),
    )


def _record_usage(worker: Factory, tenant: Tenant) -> None:
    """Report workflow: the writer's attempt (actual), the researcher's attempt (estimated), the
    analysis (mock, no agent). Survey workflow: planning (actual, no agent) and an unpriced call."""
    report = tenant.report
    _call(
        worker,
        tenant,
        "acme",
        "acme-large",
        ACME,
        workflow_id=report,
        task_id=tenant.draft,
        execution_id=tenant.draft_by_writer,
    )
    _call(
        worker,
        tenant,
        "estco",
        "est-1",
        EST,
        workflow_id=report,
        task_id=tenant.draft,
        execution_id=tenant.draft_by_researcher,
    )
    _call(worker, tenant, "mock", "mock-1", ACME, workflow_id=report, purpose="prompt_analysis")
    _call(
        worker,
        tenant,
        "acme",
        "acme-large",
        ACME,
        workflow_id=tenant.survey,
        purpose="plan_generation",
    )
    _call(
        worker,
        tenant,
        "other",
        "unpriced",
        ACME,
        workflow_id=tenant.survey,
        purpose="plan_generation",
    )


def _get(client: TestClient, tenant: Tenant, path: str, **params: Any) -> dict[str, Any]:
    response = client.get(f"{API}/workspaces/{tenant.workspace_id}/analytics{path}", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _costs(row: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
    (usd,) = row["costs"]
    assert usd["currency"] == "USD"
    return Decimal(usd["actual"]), Decimal(usd["estimated"]), Decimal(usd["mock"])


@pytest.mark.usefixtures("prices")
def test_the_overview_totals_calls_tokens_and_costs_by_basis(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    tenant = _tenant(new_client, admin_factory)
    _record_usage(worker_factory, tenant)

    body = _get(tenant.client, tenant, "/overview")
    totals = body["totals"]
    assert (totals["calls"], totals["unpriced_calls"]) == (5, 1)
    assert (totals["input_tokens"], totals["output_tokens"]) == (5000, 3000)
    # Actual: 0.0045 twice; estimated: 0.004; mock: 1.5 (never added to the others).
    assert _costs(totals) == (Decimal("0.009"), Decimal("0.004"), Decimal("1.5"))
    assert (body["workflows"], body["agents"]) == (2, 2)


@pytest.mark.usefixtures("prices")
@pytest.mark.parametrize("group_by", ["workflow", "task", "agent", "model", "purpose", "day"])
def test_every_breakdown_adds_up_to_the_overview(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory, group_by: str
) -> None:
    # Handoff scenario 17: totals reconcile with the stored usage, whatever the grouping.
    tenant = _tenant(new_client, admin_factory)
    _record_usage(worker_factory, tenant)
    overview = _get(tenant.client, tenant, "/overview")["totals"]

    body = _get(tenant.client, tenant, "/costs", group_by=group_by)
    assert body["totals"] == overview and not body["truncated"]
    rows = body["rows"]
    for field in (
        "calls",
        "unpriced_calls",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
    ):
        assert sum(r[field] for r in rows) == overview[field]
    summed = [Decimal(0)] * 3
    for row in rows:
        if row["costs"]:
            for i, amount in enumerate(_costs(row)):
                summed[i] += amount
    assert tuple(summed) == _costs(overview)


@pytest.mark.usefixtures("prices")
def test_costs_are_attributed_to_workflows_agents_and_models(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    tenant = _tenant(new_client, admin_factory)
    _record_usage(worker_factory, tenant)

    by_workflow = {
        r["key"]: r for r in _get(tenant.client, tenant, "/costs", group_by="workflow")["rows"]
    }
    report = by_workflow[str(tenant.report)]
    assert report["label"] == "Write the report" and report["calls"] == 3
    assert _costs(report) == (Decimal("0.0045"), Decimal("0.004"), Decimal("1.5"))

    agents = {r["key"]: r for r in _get(tenant.client, tenant, "/agents")["rows"]}
    assert agents[str(tenant.writer)]["label"] == "Writer"
    assert _costs(agents[str(tenant.writer)]) == (Decimal("0.0045"), Decimal(0), Decimal(0))
    assert _costs(agents[str(tenant.researcher)]) == (Decimal(0), Decimal("0.004"), Decimal(0))
    # Calls no agent made are the platform's, by purpose.
    assert agents["platform:prompt_analysis"]["label"] == "Platform: prompt_analysis"
    assert agents["platform:plan_generation"]["calls"] == 2
    assert agents["platform:plan_generation"]["unpriced_calls"] == 1

    models = {r["key"]: r for r in _get(tenant.client, tenant, "/costs", group_by="model")["rows"]}
    assert (
        models["acme/acme-large"]["calls"] == 2
        and models["acme/acme-large"]["label"] == "acme · acme-large"
    )
    assert (
        models["other/unpriced"]["costs"] == [] and models["other/unpriced"]["unpriced_calls"] == 1
    )

    tokens = _get(tenant.client, tenant, "/tokens", group_by="model")
    assert {r["key"]: r["input_tokens"] for r in tokens["rows"]}["acme/acme-large"] == 2000
    assert "costs" not in tokens["rows"][0]

    one = _get(tenant.client, tenant, f"/workflows/{tenant.report}")
    assert one["totals"]["calls"] == 3
    # Most calls first: the two attempts on Draft, then the analysis (no task).
    assert [(r["label"], r["calls"]) for r in one["by_task"]] == [("Draft", 2), ("No task", 1)]
    assert {r["key"] for r in one["by_agent"]} == {
        str(tenant.writer),
        str(tenant.researcher),
        "platform:prompt_analysis",
    }


@pytest.mark.usefixtures("prices")
def test_ranges_filter_by_when_the_call_was_made(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    tenant = _tenant(new_client, admin_factory)
    _record_usage(worker_factory, tenant)
    old = _call(worker_factory, tenant, "acme", "acme-large", ACME, workflow_id=tenant.survey)
    with session_scope(admin_factory) as s:
        s.execute(
            text("UPDATE llm_usage SET created_at = now() - interval '40 days' WHERE id = :id"),
            {"id": old},
        )
        s.commit()

    assert _get(tenant.client, tenant, "/overview")["totals"]["calls"] == 5  # last 30 days
    start = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    assert _get(tenant.client, tenant, "/overview", **{"from": start})["totals"]["calls"] == 6
    end = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    only_old = _get(tenant.client, tenant, "/overview", **{"from": start, "to": end})
    assert only_old["totals"]["calls"] == 1

    url = f"{API}/workspaces/{tenant.workspace_id}/analytics/overview"
    for params in (
        {"from": end, "to": start},  # backwards
        {"from": (datetime.now(UTC) - timedelta(days=400)).isoformat()},  # over a year
        {"from": "2026-01-01T00:00:00"},  # no time zone
    ):
        refused = tenant.client.get(url, params=params)
        assert refused.status_code == 422, params
    assert tenant.client.get(url, params={"from": end, "to": start}).json()["error"]["code"] == (
        "ANALYTICS_RANGE_INVALID"
    )


@pytest.mark.usefixtures("prices")
def test_analytics_are_for_members_of_the_workspace_only(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    tenant = _tenant(new_client, admin_factory)
    _record_usage(worker_factory, tenant)
    viewer = member(new_client, tenant.client, "VIEWER")
    assert _get(viewer, tenant, "/overview")["totals"]["calls"] == 5

    mallory = new_client()
    assert register(mallory, new_email()).status_code == 201
    base = f"{API}/workspaces/{tenant.workspace_id}/analytics"
    for path in ("/overview", "/costs", "/tokens", "/agents", f"/workflows/{tenant.report}"):
        assert mallory.get(base + path).status_code == 404, path
    # Mallory's own workspace sees none of it, and not the other's workflow.
    own = f"{API}/workspaces/{my_workspace(mallory)}/analytics"
    assert mallory.get(own + "/overview").json()["totals"]["calls"] == 0
    assert mallory.get(own + f"/workflows/{tenant.report}").status_code == 404


@pytest.mark.usefixtures("prices")
def test_breakdowns_are_truncated_but_totals_are_not(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    tenant = _tenant(new_client, admin_factory)
    _record_usage(worker_factory, tenant)

    body = _get(tenant.client, tenant, "/costs", group_by="purpose", limit=1)
    assert len(body["rows"]) == 1 and body["truncated"] is True
    assert body["totals"]["calls"] == 5


@pytest.mark.usefixtures("prices")
def test_days_are_cut_in_the_requested_time_zone(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    # ADR-0045: a call at 23:30 UTC belongs to the next day in Kolkata (+05:30) and stays on
    # the same day in New York (-05:00); UTC stays the default.
    tenant = _tenant(new_client, admin_factory)
    late = _call(worker_factory, tenant, "acme", "acme-large", ACME, workflow_id=tenant.survey)
    early = _call(worker_factory, tenant, "acme", "acme-large", ACME, workflow_id=tenant.survey)
    with session_scope(admin_factory) as s:
        for call_id, at in ((late, "2026-11-10 23:30:00+00"), (early, "2026-11-11 00:30:00+00")):
            s.execute(
                text("UPDATE llm_usage SET created_at = CAST(:at AS timestamptz) WHERE id = :id"),
                {"id": call_id, "at": at},
            )
        s.commit()
    window = {"from": "2026-11-09T00:00:00+00:00", "to": "2026-11-13T00:00:00+00:00"}

    def days(**params: str) -> list[tuple[str, int]]:
        body = _get(tenant.client, tenant, "/tokens", group_by="day", **window, **params)
        assert body["totals"]["calls"] == 2
        return [(r["key"], r["calls"]) for r in body["rows"]]

    assert days() == [("2026-11-10", 1), ("2026-11-11", 1)]
    assert days(tz="Asia/Kolkata") == [("2026-11-11", 2)]
    assert days(tz="America/New_York") == [("2026-11-10", 2)]
    body = _get(tenant.client, tenant, "/costs", group_by="day", tz="Asia/Kolkata", **window)
    assert body["time_zone"] == "Asia/Kolkata" and [r["key"] for r in body["rows"]] == [
        "2026-11-11"
    ]

    url = f"{API}/workspaces/{tenant.workspace_id}/analytics/tokens"
    for tz in ("Mars/Olympus", "../etc/passwd", "UTC'; --"):
        refused = tenant.client.get(url, params={"group_by": "day", "tz": tz})
        assert refused.status_code == 422, tz
        assert refused.json()["error"]["code"] == "ANALYTICS_TIME_ZONE_INVALID"
