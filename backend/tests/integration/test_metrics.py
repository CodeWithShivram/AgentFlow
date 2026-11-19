"""Latency, retry and failure metrics (Step 7.5, ADR-0043) against real PostgreSQL.

The records are written directly with known values, so every count, rate and percentile can
be checked exactly (percentiles interpolate like PostgreSQL's percentile_cont).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.sync_session import session_scope
from app.llm.types import CallContext, ProviderUsage
from app.models import (
    Agent,
    AgentExecution,
    AgentStatus,
    AgentVersion,
    ExecutionStatus,
    LlmCallStatus,
    ToolInvocation,
    ToolInvocationStatus,
    VerificationOutcome,
    VerificationResult,
    Workflow,
    WorkflowStatus,
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
NOW = datetime.now(UTC)


@dataclass(frozen=True)
class Tenant:
    client: TestClient
    workspace_id: uuid.UUID
    alpha: uuid.UUID
    beta: uuid.UUID


def _attempt(
    s: Session,
    task: WorkflowTask,
    agent: tuple[uuid.UUID, uuid.UUID],
    number: int,
    status: ExecutionStatus,
    seconds: float | None,
    *,
    ended: datetime = NOW,
) -> AgentExecution:
    execution = AgentExecution(
        workspace_id=task.workspace_id,
        workflow_id=task.workflow_id,
        task_id=task.id,
        agent_id=agent[0],
        agent_version_id=agent[1],
        attempt_number=number,
        status=status,
        dispatch_event_id=uuid.uuid4(),
        started_at=ended - timedelta(seconds=seconds or 1),
        completed_at=ended if status != ExecutionStatus.RUNNING else None,
    )
    s.add(execution)
    s.flush()
    return execution


def _result(
    s: Session, execution: AgentExecution, verifier: str, outcome: VerificationOutcome
) -> None:
    s.add(
        VerificationResult(
            workspace_id=execution.workspace_id,
            workflow_id=execution.workflow_id,
            task_id=execution.task_id,
            execution_id=execution.id,
            verifier=verifier,
            outcome=outcome,
            checks=[],
            issues=[],
        )
    )
    s.flush()


def _tenant(new_client: ClientFactory, admin: Factory, worker: Factory) -> Tenant:
    client = new_client()
    assert register(client, new_email()).status_code == 201
    workspace_id = uuid.UUID(my_workspace(client))
    user_id = uuid.UUID(client.get(f"{API}/auth/me").json()["user"]["id"])
    passed, failed = VerificationOutcome.PASSED, VerificationOutcome.FAILED
    with session_scope(admin) as s:
        agents = {}
        for name in ("Alpha", "Beta"):
            agent = Agent(workspace_id=workspace_id, name=name, category="writing")
            s.add(agent)
            s.flush()
            version = AgentVersion(agent_id=agent.id, version=1, configuration={"capabilities": []})
            s.add(version)
            s.flush()
            agent.active_version_id, agent.status = version.id, AgentStatus.ACTIVE
            agents[name] = (agent.id, version.id)
        running = Workflow(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            prompt="running",
            status=WorkflowStatus.RUNNING,
            started_at=NOW - timedelta(minutes=5),
        )
        done = Workflow(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            prompt="done",
            status=WorkflowStatus.COMPLETED,
            started_at=NOW - timedelta(seconds=60),
            completed_at=NOW,
        )
        dropped = Workflow(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            prompt="dropped",
            status=WorkflowStatus.CANCELLED,
            completed_at=NOW,
        )
        s.add_all([running, done, dropped])
        s.flush()
        tasks = []
        for title in ("retried", "first try", "cancelled", "in flight"):
            task = WorkflowTask(
                workspace_id=workspace_id, workflow_id=running.id, title=title, description="d"
            )
            s.add(task)
            s.flush()
            tasks.append(task)
        retried, first_try, cancelled, in_flight = tasks
        # Alpha: a failed first attempt (10 s), then a successful retry (20 s), plus an old one.
        a1 = _attempt(s, retried, agents["Alpha"], 1, ExecutionStatus.FAILED, 10)
        a2 = _attempt(s, retried, agents["Alpha"], 2, ExecutionStatus.SUCCEEDED, 20)
        _attempt(
            s,
            retried,
            agents["Alpha"],
            3,
            ExecutionStatus.FAILED,
            99,
            ended=NOW - timedelta(days=40),
        )
        # Beta: right first time (5 s), a cancelled attempt (30 s), one still running.
        b1 = _attempt(s, first_try, agents["Beta"], 1, ExecutionStatus.SUCCEEDED, 5)
        _attempt(s, cancelled, agents["Beta"], 1, ExecutionStatus.CANCELLED, 30)
        _attempt(s, in_flight, agents["Beta"], 1, ExecutionStatus.RUNNING, None)
        _result(s, a1, "deterministic/v1", failed)
        for execution in (a2, b1):
            _result(s, execution, "deterministic/v1", passed)
            _result(s, execution, "semantic/v1", passed)
        for task in (retried, first_try):
            s.execute(
                text(
                    "UPDATE workflow_tasks SET status = 'COMPLETED', completed_at = now() "
                    "WHERE id = :id"
                ),
                {"id": task.id},
            )
        for name, status, ms in (
            ("web_search", ToolInvocationStatus.SUCCEEDED, 5),
            ("web_search", ToolInvocationStatus.FAILED, 15),
            ("web_search", ToolInvocationStatus.DENIED, 0),
        ):
            s.add(
                ToolInvocation(
                    workspace_id=workspace_id,
                    workflow_id=running.id,
                    task_id=retried.id,
                    execution_id=a2.id,
                    tool_name=name,
                    status=status,
                    error_code=None if status == ToolInvocationStatus.SUCCEEDED else "X",
                    duration_ms=ms,
                    input_hash="0" * 64,
                )
            )
        s.commit()
    ok, failing = LlmCallStatus.SUCCEEDED, LlmCallStatus.FAILED
    for latency, call_status in ((100, ok), (200, ok), (300, ok), (400, failing)):
        record_model_call(
            worker,
            CallRecord(
                context=CallContext(workspace_id=workspace_id),
                purpose="test",
                provider="mock",
                model="mock-1",
                status=call_status,
                usage=ProviderUsage(1, 1),
                provider_request_id=None,
                error_code=None if call_status == ok else "MODEL_CALL_FAILED",
                latency_ms=latency,
            ),
        )
    return Tenant(client, workspace_id, agents["Alpha"][0], agents["Beta"][0])


def _metrics(client: TestClient, workspace_id: uuid.UUID, **params: Any) -> dict[str, Any]:
    response = client.get(f"{API}/workspaces/{workspace_id}/analytics/metrics", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": round(numerator / denominator, 6) if denominator else None,
    }


def test_metrics_count_rates_and_exact_percentiles(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    tenant = _tenant(new_client, admin_factory, worker_factory)
    body = _metrics(tenant.client, tenant.workspace_id)

    calls = body["model_calls"]
    assert calls["calls"] == 4 and calls["by_status"] == {"SUCCEEDED": 3, "FAILED": 1}
    assert calls["failure_rate"] == _rate(1, 4)
    assert calls["latency"] == {
        "unit": "ms",
        "samples": 4,
        "p50": 250.0,
        "p95": 385.0,
        "max": 400.0,
        "mean": 250.0,
    }
    assert [g["key"] for g in calls["by_model"]] == ["mock/mock-1"]

    attempts = body["attempts"]
    # The 40-day-old attempt is outside the range; the running one is in flight.
    assert attempts["finished"] == 4 and attempts["in_flight"] == 1
    assert attempts["by_status"] == {"FAILED": 1, "SUCCEEDED": 2, "CANCELLED": 1}
    assert attempts["failure_rate"] == _rate(1, 3)  # cancelled is not a failure
    assert attempts["retry_rate"] == _rate(1, 4)
    duration = attempts["duration"]
    assert (duration["unit"], duration["samples"]) == ("s", 4)
    assert (duration["p50"], duration["p95"], duration["max"], duration["mean"]) == (
        15.0,
        28.5,
        30.0,
        16.25,
    )
    by_agent = {g["label"]: g for g in attempts["by_agent"]}
    assert by_agent["Alpha"]["failure_rate"] == _rate(1, 2)
    assert by_agent["Alpha"]["retry_rate"] == _rate(1, 2)
    assert by_agent["Beta"]["failure_rate"] == _rate(0, 1)

    assert body["tasks"] == {"completed": 2, "first_attempt": _rate(1, 2)}
    assert body["verification"] == {
        "deterministic_pass_rate": _rate(2, 3),
        "semantic_pass_rate": _rate(2, 2),
    }
    tools = body["tools"]
    assert (tools["calls"], tools["failure_rate"], tools["denial_rate"]) == (
        3,
        _rate(1, 3),
        _rate(1, 3),
    )
    assert tools["duration"]["p50"] == 5.0 and tools["by_tool"][0]["key"] == "web_search"

    workflows = body["workflows"]
    assert workflows["by_status"] == {"COMPLETED": 1, "CANCELLED": 1}
    assert (workflows["finished"], workflows["in_flight"]) == (2, 1)
    # The cancelled workflow never started: it has no duration.
    assert (workflows["duration"]["samples"], workflows["duration"]["p50"]) == (1, 60.0)


def test_nothing_measured_is_null_not_zero(new_client: ClientFactory) -> None:
    client = new_client()
    assert register(client, new_email()).status_code == 201
    body = _metrics(client, uuid.UUID(my_workspace(client)))
    assert body["model_calls"]["failure_rate"] == _rate(0, 0)
    assert body["model_calls"]["failure_rate"]["value"] is None
    assert body["attempts"]["duration"] == {
        "unit": "s",
        "samples": 0,
        "p50": None,
        "p95": None,
        "max": None,
        "mean": None,
    }
    assert body["tasks"]["first_attempt"]["value"] is None
    assert body["verification"]["semantic_pass_rate"]["value"] is None


def test_the_range_decides_what_counts(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    tenant = _tenant(new_client, admin_factory, worker_factory)
    start = (NOW - timedelta(days=60)).isoformat()
    end = (NOW - timedelta(days=30)).isoformat()
    old = _metrics(tenant.client, tenant.workspace_id, **{"from": start, "to": end})
    assert old["attempts"]["finished"] == 1 and old["attempts"]["failure_rate"] == _rate(1, 1)
    assert old["model_calls"]["calls"] == 0
    url = f"{API}/workspaces/{tenant.workspace_id}/analytics/metrics"
    assert tenant.client.get(url, params={"from": end, "to": start}).status_code == 422


def test_metrics_are_for_members_of_the_workspace_only(
    new_client: ClientFactory, admin_factory: Factory, worker_factory: Factory
) -> None:
    tenant = _tenant(new_client, admin_factory, worker_factory)
    viewer = member(new_client, tenant.client, "VIEWER")
    assert _metrics(viewer, tenant.workspace_id)["model_calls"]["calls"] == 4
    mallory = new_client()
    assert register(mallory, new_email()).status_code == 201
    url = f"{API}/workspaces/{tenant.workspace_id}/analytics/metrics"
    assert mallory.get(url).status_code == 404
    assert _metrics(mallory, uuid.UUID(my_workspace(mallory)))["attempts"]["finished"] == 0
