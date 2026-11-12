"""Workflows and tasks through the HTTP API: lifecycle, DAG rules, RBAC, isolation, idempotency."""

import uuid
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from app.core.config import Settings
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


def wf_url(workspace: str, suffix: str = "") -> str:
    return f"{API}/workspaces/{workspace}/workflows{suffix}"


def create_workflow(
    client: TestClient, workspace: str, key: str | None = None, **settings: Any
) -> httpx2.Response:
    headers = {**csrf(client), **({"Idempotency-Key": key} if key else {})}
    body = {"prompt": "Research AI support platforms", "settings": settings}
    response: httpx2.Response = client.post(wf_url(workspace), json=body, headers=headers)
    return response


def get_workflow(client: TestClient, workspace: str, workflow_id: str) -> dict[str, Any]:
    body: dict[str, Any] = client.get(wf_url(workspace, f"/{workflow_id}")).json()
    return body


def move(client: TestClient, workspace: str, workflow_id: str, to: str) -> httpx2.Response:
    revision = get_workflow(client, workspace, workflow_id)["revision"]
    response: httpx2.Response = client.post(
        wf_url(workspace, f"/{workflow_id}/transitions"),
        json={"to_status": to, "revision": revision},
        headers=csrf(client),
    )
    return response


def add_task(
    client: TestClient, workspace: str, workflow_id: str, title: str = "task", **fields: Any
) -> httpx2.Response:
    response: httpx2.Response = client.post(
        wf_url(workspace, f"/{workflow_id}/tasks"),
        json={"title": title, "required_skills": ["report_writing"], **fields},
        headers=csrf(client),
    )
    return response


def depend(
    client: TestClient, workspace: str, workflow_id: str, task: str, on: str
) -> httpx2.Response:
    response: httpx2.Response = client.post(
        wf_url(workspace, f"/{workflow_id}/tasks/{task}/dependencies"),
        json={"depends_on_task_id": on},
        headers=csrf(client),
    )
    return response


def task_of(client: TestClient, workspace: str, workflow_id: str, task_id: str) -> dict[str, Any]:
    body: dict[str, Any] = client.get(wf_url(workspace, f"/{workflow_id}/tasks/{task_id}")).json()
    return body


def events(client: TestClient, workspace: str, workflow_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = client.get(wf_url(workspace, f"/{workflow_id}/events")).json()[
        "items"
    ]
    return items


def builtin_id(client: TestClient, workspace: str, name: str) -> str:
    agents = client.get(f"{API}/workspaces/{workspace}/agents").json()["items"]
    agent_id: str = next(a["id"] for a in agents if a["name"] == name)
    return agent_id


@pytest.fixture
def owner(new_client: ClientFactory, seeded_builtins: Settings) -> TestClient:
    client = new_client()
    register(client, new_email())
    return client


@pytest.fixture
def workspace(owner: TestClient) -> str:
    return my_workspace(owner)


@pytest.fixture
def workflow(owner: TestClient, workspace: str) -> str:
    workflow_id: str = create_workflow(owner, workspace, autonomy_policy="autonomous").json()["id"]
    return workflow_id


# --- creation, budget and idempotency ---------------------------------------------------------


def test_create_persists_settings_and_budget_exactly(
    owner: TestClient, workspace: str, api_settings: Settings
) -> None:
    response = create_workflow(
        owner,
        workspace,
        budget_limit="2.5",
        currency="eur",
        max_parallel_tasks=4,
        allow_dynamic_agent_proposals=True,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING" and body["revision"] == 1
    assert Decimal(body["budget_limit"]) == Decimal("2.5")
    assert body["currency"] == "EUR" and body["max_parallel_tasks"] == 4
    assert body["autonomy_policy"] == "approval_required"
    stored = sql(
        api_settings, "SELECT budget_limit, currency FROM workflows WHERE id = :id", id=body["id"]
    )
    assert stored == [(Decimal("2.5000"), "EUR")]


def test_max_budget_alias_is_rejected(owner: TestClient, workspace: str) -> None:
    response = create_workflow(owner, workspace, max_budget="2.00")

    assert response.status_code == 422


def test_idempotent_create_replays_and_rejects_a_different_payload(
    owner: TestClient, workspace: str, api_settings: Settings
) -> None:
    first = create_workflow(owner, workspace, key="req-123", budget_limit="5")
    replay = create_workflow(owner, workspace, key="req-123", budget_limit="5")
    conflict = create_workflow(owner, workspace, key="req-123", budget_limit="6")

    assert first.status_code == replay.status_code == 201
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert sql(api_settings, "SELECT count(*) FROM workflows") == [(1,)]


def test_viewers_cannot_create_workflows(
    new_client: ClientFactory, owner: TestClient, workspace: str
) -> None:
    viewer = member(new_client, owner, "VIEWER")

    response = create_workflow(viewer, workspace)

    assert response.status_code == 403
    assert viewer.get(wf_url(workspace)).status_code == 200


# --- lifecycle through the API ----------------------------------------------------------------


def test_plan_approval_start_and_assignment_pin_the_agent_version(
    new_client: ClientFactory, owner: TestClient, workspace: str, api_settings: Settings
) -> None:
    proposer = member(new_client, owner, "MEMBER")
    workflow_id = create_workflow(proposer, workspace).json()["id"]  # approval_required
    first = add_task(proposer, workspace, workflow_id, "research").json()
    second = add_task(proposer, workspace, workflow_id, "write", depends_on=[first["id"]]).json()
    assert second["depends_on"] == [first["id"]]

    assert move(proposer, workspace, workflow_id, "PLANNING").status_code == 200
    direct = move(proposer, workspace, workflow_id, "READY")
    assert direct.status_code == 409  # approval required
    assert move(proposer, workspace, workflow_id, "AWAITING_APPROVAL").status_code == 200
    by_member = move(proposer, workspace, workflow_id, "READY")
    assert by_member.status_code == 403 and by_member.json()["error"]["code"] == "INSUFFICIENT_ROLE"
    assert move(owner, workspace, workflow_id, "READY").status_code == 200

    started = proposer.post(
        wf_url(workspace, f"/{workflow_id}/start"),
        json={"revision": get_workflow(owner, workspace, workflow_id)["revision"]},
        headers=csrf(proposer),
    )
    assert started.status_code == 200 and started.json()["status"] == "RUNNING"
    assert started.json()["started_at"] is not None
    assert task_of(owner, workspace, workflow_id, first["id"])["status"] == "READY"
    assert task_of(owner, workspace, workflow_id, second["id"])["status"] == "PENDING"

    writer = builtin_id(owner, workspace, "Writer")
    task = task_of(owner, workspace, workflow_id, first["id"])
    assigned = proposer.post(
        wf_url(workspace, f"/{workflow_id}/tasks/{first['id']}/assign"),
        json={"agent_id": writer, "revision": task["revision"]},
        headers=csrf(proposer),
    )
    assert assigned.status_code == 200
    assert assigned.json()["status"] == "ASSIGNED"
    version_id = sql(
        api_settings, "SELECT active_version_id::text FROM agents WHERE id = :id", id=writer
    )[0][0]
    assert assigned.json()["assigned_agent_version_id"] == version_id

    blocked = proposer.post(
        wf_url(workspace, f"/{workflow_id}/tasks/{second['id']}/assign"),
        json={
            "agent_id": writer,
            "revision": task_of(owner, workspace, workflow_id, second["id"])["revision"],
        },
        headers=csrf(proposer),
    )
    assert blocked.status_code == 409  # dependencies not completed, so not READY


def test_assignment_rejects_agents_without_the_skills(
    owner: TestClient, workspace: str, workflow: str
) -> None:
    task = add_task(owner, workspace, workflow, required_skills=["code_generation"]).json()
    move(owner, workspace, workflow, "PLANNING")
    move(owner, workspace, workflow, "READY")
    move(owner, workspace, workflow, "RUNNING")
    task = task_of(owner, workspace, workflow, task["id"])

    response = owner.post(
        wf_url(workspace, f"/{workflow}/tasks/{task['id']}/assign"),
        json={"agent_id": builtin_id(owner, workspace, "Writer"), "revision": task["revision"]},
        headers=csrf(owner),
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"] == {"missing_skills": ["code_generation"]}


def test_users_cannot_force_completion_or_skip_states(
    owner: TestClient, workspace: str, workflow: str
) -> None:
    skip = move(owner, workspace, workflow, "RUNNING")
    add_task(owner, workspace, workflow)
    for status in ("PLANNING", "READY", "RUNNING"):
        move(owner, workspace, workflow, status)
    complete = move(owner, workspace, workflow, "COMPLETED")

    assert skip.status_code == 409 and skip.json()["error"]["code"] == "INVALID_TRANSITION"
    assert complete.status_code == 409 and complete.json()["error"]["code"] == "INVALID_TRANSITION"


def test_stale_revision_is_rejected(owner: TestClient, workspace: str, workflow: str) -> None:
    response = owner.post(
        wf_url(workspace, f"/{workflow}/transitions"),
        json={"to_status": "PLANNING", "revision": 99},
        headers=csrf(owner),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WORKFLOW_REVISION_CONFLICT"
    assert response.json()["error"]["details"] == {"current_revision": 1}


def test_cancel_cancels_every_unfinished_task(
    owner: TestClient, workspace: str, workflow: str
) -> None:
    tasks = [add_task(owner, workspace, workflow, f"t{i}").json()["id"] for i in range(3)]
    for status in ("PLANNING", "READY", "RUNNING"):
        move(owner, workspace, workflow, status)

    response = owner.post(
        wf_url(workspace, f"/{workflow}/cancel"),
        json={
            "revision": get_workflow(owner, workspace, workflow)["revision"],
            "reason": "no longer needed",
        },
        headers=csrf(owner),
    )

    assert response.status_code == 200 and response.json()["status"] == "CANCELLED"
    assert {task_of(owner, workspace, workflow, t)["status"] for t in tasks} == {"CANCELLED"}
    assert move(owner, workspace, workflow, "RUNNING").status_code == 409
    assert events(owner, workspace, workflow)[-4]["event_type"] == "WORKFLOW_CANCELLED"


# --- plan editing and the DAG -------------------------------------------------------------------


def test_dependency_rules(
    new_client: ClientFactory, owner: TestClient, workspace: str, workflow: str
) -> None:
    a, b, c = (add_task(owner, workspace, workflow, n).json()["id"] for n in "abc")
    other_workflow = create_workflow(owner, workspace).json()["id"]
    foreign = add_task(owner, workspace, other_workflow).json()["id"]
    stranger = new_client()
    register(stranger, new_email())
    stranger_ws = my_workspace(stranger)
    stranger_task = add_task(
        stranger, stranger_ws, create_workflow(stranger, stranger_ws).json()["id"]
    ).json()["id"]

    assert depend(owner, workspace, workflow, b, a).status_code == 201
    assert depend(owner, workspace, workflow, c, b).status_code == 201
    cases = {
        "self": (depend(owner, workspace, workflow, a, a), 422, "INVALID_DEPENDENCY"),
        "duplicate": (depend(owner, workspace, workflow, b, a), 409, "DEPENDENCY_EXISTS"),
        "two-cycle": (depend(owner, workspace, workflow, a, b), 422, "DEPENDENCY_CYCLE"),
        "three-cycle": (depend(owner, workspace, workflow, a, c), 422, "DEPENDENCY_CYCLE"),
        "cross-workflow": (
            depend(owner, workspace, workflow, a, foreign),
            422,
            "INVALID_DEPENDENCY",
        ),
        "cross-workspace": (
            depend(owner, workspace, workflow, a, stranger_task),
            422,
            "INVALID_DEPENDENCY",
        ),
        "unknown": (
            depend(owner, workspace, workflow, a, str(uuid.uuid4())),
            422,
            "INVALID_DEPENDENCY",
        ),
    }
    for name, (response, status, code) in cases.items():
        assert response.status_code == status, name
        assert response.json()["error"]["code"] == code, name


def test_removing_a_task_keeps_history_and_drops_its_edges(
    owner: TestClient, workspace: str, workflow: str
) -> None:
    a = add_task(owner, workspace, workflow, "a").json()
    b = add_task(owner, workspace, workflow, "b", depends_on=[a["id"]]).json()

    removed = owner.delete(
        wf_url(workspace, f"/{workflow}/tasks/{a['id']}"),
        params={"revision": a["revision"]},
        headers=csrf(owner),
    )

    assert removed.status_code == 204
    gone = task_of(owner, workspace, workflow, a["id"])
    assert gone["removed"] and gone["status"] == "CANCELLED"
    assert task_of(owner, workspace, workflow, b["id"])["depends_on"] == []
    assert depend(owner, workspace, workflow, b["id"], a["id"]).status_code == 422
    assert events(owner, workspace, workflow)[-1]["event_type"] == "TASK_REMOVED"


def test_editing_an_approved_plan_sends_it_back_to_planning(
    owner: TestClient, workspace: str, workflow: str
) -> None:
    task = add_task(owner, workspace, workflow).json()
    move(owner, workspace, workflow, "PLANNING")
    move(owner, workspace, workflow, "READY")

    edited = owner.patch(
        wf_url(workspace, f"/{workflow}/tasks/{task['id']}"),
        json={"revision": task["revision"], "title": "revised"},
        headers=csrf(owner),
    )

    assert edited.status_code == 200 and edited.json()["title"] == "revised"
    assert get_workflow(owner, workspace, workflow)["status"] == "PLANNING"


def test_plan_is_frozen_while_running(owner: TestClient, workspace: str, workflow: str) -> None:
    task = add_task(owner, workspace, workflow).json()
    for status in ("PLANNING", "READY", "RUNNING"):
        move(owner, workspace, workflow, status)

    responses = [
        add_task(owner, workspace, workflow, "late"),
        owner.patch(
            wf_url(workspace, f"/{workflow}/tasks/{task['id']}"),
            json={
                "revision": task_of(owner, workspace, workflow, task["id"])["revision"],
                "title": "x",
            },
            headers=csrf(owner),
        ),
    ]

    for response in responses:
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "PLAN_NOT_EDITABLE"


def test_stale_task_revision_is_rejected(owner: TestClient, workspace: str, workflow: str) -> None:
    task = add_task(owner, workspace, workflow).json()
    url = wf_url(workspace, f"/{workflow}/tasks/{task['id']}")
    owner.patch(url, json={"revision": 1, "title": "first"}, headers=csrf(owner))

    stale = owner.patch(url, json={"revision": 1, "title": "second"}, headers=csrf(owner))

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "TASK_REVISION_CONFLICT"


# --- isolation and events ------------------------------------------------------------------------


def test_workflows_are_invisible_across_workspaces(
    new_client: ClientFactory, owner: TestClient, workspace: str, workflow: str
) -> None:
    task = add_task(owner, workspace, workflow).json()
    mallory = new_client()
    register(mallory, new_email())
    theirs = my_workspace(mallory)

    via_own = [
        mallory.get(wf_url(theirs, f"/{workflow}")),
        mallory.get(wf_url(theirs, f"/{workflow}/tasks")),
        mallory.get(wf_url(theirs, f"/{workflow}/events")),
        mallory.post(
            wf_url(theirs, f"/{workflow}/transitions"),
            json={"to_status": "CANCELLED", "revision": 1},
            headers=csrf(mallory),
        ),
        add_task(mallory, theirs, workflow),
    ]
    via_victim = [
        mallory.get(wf_url(workspace, f"/{workflow}")),
        mallory.get(wf_url(workspace, f"/{workflow}/tasks/{task['id']}")),
    ]

    for response in via_own:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "WORKFLOW_NOT_FOUND"
    for response in via_victim:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"
    assert get_workflow(owner, workspace, workflow)["status"] == "PENDING"


def test_events_are_ordered_paginated_append_only_and_free_of_prompts(
    owner: TestClient, workspace: str, workflow: str, api_settings: Settings
) -> None:
    add_task(owner, workspace, workflow)
    move(owner, workspace, workflow, "PLANNING")

    page = owner.get(wf_url(workspace, f"/{workflow}/events"), params={"limit": 2}).json()
    rest = owner.get(
        wf_url(workspace, f"/{workflow}/events"), params={"after": page["last_sequence"]}
    ).json()

    types = [e["event_type"] for e in page["items"] + rest["items"]]
    assert types == ["WORKFLOW_CREATED", "TASK_CREATED", "WORKFLOW_STATUS_CHANGED"]
    assert rest["items"][-1]["payload"] == {"from": "PENDING", "to": "PLANNING", "actor": "USER"}
    assert "Research AI support platforms" not in str(page) + str(rest)
    with pytest.raises(DBAPIError, match="append-only"):
        sql(api_settings, "UPDATE workflow_events SET payload = '{}'::jsonb")


# --- read-only endpoints added for the frontend (Step 1.9) ---------------------------------------


def test_agent_catalog_matches_platform_policy(
    owner: TestClient, new_client: ClientFactory
) -> None:
    from app.agents.catalog import CAPABILITIES, CATEGORIES, TOOLS

    catalog = owner.get(f"{API}/agent-catalog")
    anonymous = new_client().get(f"{API}/agent-catalog")

    assert catalog.status_code == 200
    body = catalog.json()
    assert body["capabilities"] == sorted(CAPABILITIES)
    assert body["categories"] == sorted(CATEGORIES)
    assert {t["name"]: t["risk"] for t in body["tools"]} == {
        n: t.risk.value for n, t in TOOLS.items()
    }
    assert anonymous.status_code == 401


def test_execution_history_lists_attempts_with_agent_versions(
    owner: TestClient, workspace: str, workflow: str, worker_factory: Any
) -> None:
    from app.db.sync_session import session_scope
    from app.execution import runner

    task = add_task(owner, workspace, workflow).json()
    url = wf_url(workspace, f"/{workflow}/tasks/{task['id']}/executions")
    assert owner.get(url).json() == {"items": []}
    for status in ("PLANNING", "READY", "RUNNING"):
        move(owner, workspace, workflow, status)
    task = task_of(owner, workspace, workflow, task["id"])
    owner.post(
        wf_url(workspace, f"/{workflow}/tasks/{task['id']}/assign"),
        json={"agent_id": builtin_id(owner, workspace, "Writer"), "revision": task["revision"]},
        headers=csrf(owner),
    )
    with session_scope(worker_factory) as s:
        from sqlalchemy import select

        from app.models import OutboxEvent

        event = s.execute(select(OutboxEvent)).scalar_one()
    with session_scope(worker_factory) as s:
        claim = runner.claim(
            s,
            event_id=event.id,
            workspace_id=event.workspace_id,
            workflow_id=uuid.UUID(workflow),
            task_id=uuid.UUID(task["id"]),
            agent_version_id=uuid.UUID(event.payload["agent_version_id"]),
        )
    assert isinstance(claim, runner.Claim)
    with session_scope(worker_factory) as s:
        runner.finish(s, claim, result=runner.run_mock_agent(claim), failure=None)

    (attempt,) = owner.get(url).json()["items"]

    assert attempt["attempt_number"] == 1 and attempt["status"] == "SUCCEEDED"
    assert attempt["agent_version"] == 1 and attempt["error_code"] is None
    assert attempt["result_summary"].startswith("Mock result")
    assert task_of(owner, workspace, workflow, task["id"])["status"] == "SUBMITTED"


def test_execution_history_is_workspace_scoped(
    new_client: ClientFactory, owner: TestClient, workspace: str, workflow: str
) -> None:
    task = add_task(owner, workspace, workflow).json()
    mallory = new_client()
    register(mallory, new_email())

    via_own = mallory.get(
        wf_url(my_workspace(mallory), f"/{workflow}/tasks/{task['id']}/executions")
    )
    via_victim = mallory.get(wf_url(workspace, f"/{workflow}/tasks/{task['id']}/executions"))

    assert via_own.status_code == 404 and via_own.json()["error"]["code"] == "WORKFLOW_NOT_FOUND"
    assert via_victim.status_code == 404
