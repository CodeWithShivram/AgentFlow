"""Agent registry through the HTTP API: lifecycle, versioning, RBAC, isolation, proposals, audit."""

import uuid
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

CONFIG: dict[str, Any] = {
    "system_instruction_ref": "workspace://prompts/summarizer/v1",
    "capabilities": ["report_writing", "editing"],
    "tools": [{"tool": "artifact_writer", "access": "write"}],
}
# A running workflow with one task pinned to (:agent, :version).
ASSIGNED_TASK_SQL = """
WITH w AS (
    INSERT INTO workflows (workspace_id, created_by_user_id, prompt, status, autonomy_policy)
    VALUES (:ws, :user, 'p', 'RUNNING', 'approval_required') RETURNING id
)
INSERT INTO workflow_tasks (workspace_id, workflow_id, title, description, status,
                            assigned_agent_id, assigned_agent_version_id)
SELECT :ws, id, 't', 'd', 'IN_PROGRESS', :agent, :version FROM w
"""
HIGH_RISK_CONFIG: dict[str, Any] = {
    **CONFIG,
    "capabilities": ["test_execution"],
    "tools": [{"tool": "test_runner", "access": "read"}],
}


def agents_url(workspace_id: str, suffix: str = "") -> str:
    return f"{API}/workspaces/{workspace_id}/agents{suffix}"


def create(
    client: TestClient, workspace_id: str, name: str = "Summarizer", config: dict[str, Any] = CONFIG
) -> httpx2.Response:
    response: httpx2.Response = client.post(
        agents_url(workspace_id),
        json={"name": name, "category": "writing", "description": "d", "configuration": config},
        headers=csrf(client),
    )
    return response


def act(
    client: TestClient, workspace_id: str, agent: dict[str, Any], action: str
) -> httpx2.Response:
    response: httpx2.Response = client.post(
        agents_url(workspace_id, f"/{agent['id']}/{action}"),
        json={"revision": agent["revision"]},
        headers=csrf(client),
    )
    return response


def patch(client: TestClient, workspace_id: str, agent_id: str, **body: Any) -> httpx2.Response:
    response: httpx2.Response = client.patch(
        agents_url(workspace_id, f"/{agent_id}"), json=body, headers=csrf(client)
    )
    return response


def fetch(client: TestClient, workspace_id: str, agent_id: str) -> dict[str, Any]:
    body: dict[str, Any] = client.get(agents_url(workspace_id, f"/{agent_id}")).json()
    return body


def propose(
    client: TestClient, workspace_id: str, config: dict[str, Any] = CONFIG, **extra: Any
) -> httpx2.Response:
    response: httpx2.Response = client.post(
        f"{API}/workspaces/{workspace_id}/agent-proposals",
        json={
            "name": "Market scanner",
            "category": "research",
            "rationale": "Recurring competitive analysis needs a dedicated specialist.",
            "configuration": config,
            **extra,
        },
        headers=csrf(client),
    )
    return response


def decide(
    client: TestClient, workspace_id: str, proposal_id: str, decision: str
) -> httpx2.Response:
    response: httpx2.Response = client.post(
        f"{API}/workspaces/{workspace_id}/agent-proposals/{proposal_id}/{decision}",
        json={"note": f"{decision}d after review"},
        headers=csrf(client),
    )
    return response


@pytest.fixture
def owner(new_client: ClientFactory, seeded_builtins: Settings) -> TestClient:
    client = new_client()
    register(client, new_email())
    return client


@pytest.fixture
def workspace(owner: TestClient) -> str:
    return my_workspace(owner)


# --- built-ins ------------------------------------------------------------------------------


def test_every_workspace_sees_the_six_active_builtins(owner: TestClient, workspace: str) -> None:
    page = owner.get(agents_url(workspace)).json()

    builtins = [a for a in page["items"] if a["is_builtin"]]
    assert {a["name"] for a in builtins} == {
        "Researcher",
        "Analyst",
        "Developer",
        "Writer",
        "QA",
        "General",
    }
    assert all(a["status"] == "ACTIVE" and a["active_version"] == 1 for a in builtins)
    assert all(a["workspace_id"] is None for a in builtins)


def test_builtins_are_read_only_to_workspaces(owner: TestClient, workspace: str) -> None:
    builtin = next(a for a in owner.get(agents_url(workspace)).json()["items"] if a["is_builtin"])

    responses = [
        patch(owner, workspace, builtin["id"], revision=builtin["revision"], description="x"),
        act(owner, workspace, builtin, "publish"),
        act(owner, workspace, builtin, "deactivate"),
    ]

    for response in responses:
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "AGENT_READ_ONLY"


# --- lifecycle and versioning ---------------------------------------------------------------


def test_create_publish_activate_lifecycle(owner: TestClient, workspace: str) -> None:
    created = create(owner, workspace)
    assert created.status_code == 201
    agent = created.json()
    assert agent["status"] == "INACTIVE"
    assert agent["active_version"] is None and agent["has_unpublished_changes"]

    premature = act(owner, workspace, agent, "activate")
    assert premature.status_code == 409
    assert premature.json()["error"]["code"] == "AGENT_NOT_PUBLISHED"

    published = act(owner, workspace, agent, "publish")
    assert published.status_code == 201 and published.json()["version"] == 1
    agent = fetch(owner, workspace, agent["id"])
    assert agent["active_version"] == 1 and not agent["has_unpublished_changes"]

    assert act(owner, workspace, agent, "publish").json()["error"]["code"] == "NOTHING_TO_PUBLISH"
    activated = act(owner, workspace, agent, "activate")
    assert activated.status_code == 200 and activated.json()["status"] == "ACTIVE"


def test_published_versions_are_immutable_history(
    owner: TestClient, workspace: str, api_settings: Settings
) -> None:
    agent = create(owner, workspace).json()
    act(owner, workspace, agent, "publish")
    agent = fetch(owner, workspace, agent["id"])
    edited = {**CONFIG, "capabilities": ["documentation"]}
    agent = patch(
        owner, workspace, agent["id"], revision=agent["revision"], configuration=edited
    ).json()
    assert agent["has_unpublished_changes"] and agent["active_version"] == 1
    act(owner, workspace, agent, "publish")

    versions = owner.get(agents_url(workspace, f"/{agent['id']}/versions")).json()

    assert [v["version"] for v in versions["items"]] == [2, 1]
    assert versions["items"][1]["configuration"]["capabilities"] == ["report_writing", "editing"]
    assert versions["items"][0]["configuration"]["capabilities"] == ["documentation"]
    with pytest.raises(DBAPIError, match="immutable"):
        sql(
            api_settings,
            "UPDATE agent_versions SET configuration = '{}'::jsonb WHERE agent_id = :id",
            id=agent["id"],
        )


def test_tasks_keep_the_exact_version_they_were_assigned(
    owner: TestClient, workspace: str, api_settings: Settings
) -> None:
    agent = create(owner, workspace).json()
    v1 = act(owner, workspace, agent, "publish").json()
    agent = fetch(owner, workspace, agent["id"])
    user_id = owner.get(f"{API}/auth/me").json()["user"]["id"]
    sql(
        api_settings,
        ASSIGNED_TASK_SQL,
        ws=workspace,
        user=user_id,
        agent=agent["id"],
        version=v1["id"],
    )

    patch(
        owner,
        workspace,
        agent["id"],
        revision=agent["revision"],
        configuration={**CONFIG, "capabilities": ["documentation"]},
    )
    act(owner, workspace, fetch(owner, workspace, agent["id"]), "publish")

    pinned = sql(api_settings, "SELECT assigned_agent_version_id::text FROM workflow_tasks")
    assert pinned == [(v1["id"],)]
    assert fetch(owner, workspace, agent["id"])["active_version"] == 2
    with pytest.raises(DBAPIError):  # a referenced version can never disappear
        sql(api_settings, "DELETE FROM agent_versions WHERE id = :id", id=v1["id"])


def test_task_cannot_pin_a_version_of_a_different_agent(
    owner: TestClient, workspace: str, api_settings: Settings
) -> None:
    first = create(owner, workspace, "First").json()
    second = create(owner, workspace, "Second").json()
    v_first = act(owner, workspace, first, "publish").json()
    user_id = owner.get(f"{API}/auth/me").json()["user"]["id"]

    with pytest.raises(DBAPIError):
        sql(
            api_settings,
            ASSIGNED_TASK_SQL,
            ws=workspace,
            user=user_id,
            agent=second["id"],
            version=v_first["id"],
        )


def test_stale_revision_is_rejected(owner: TestClient, workspace: str) -> None:
    agent = create(owner, workspace).json()
    assert patch(owner, workspace, agent["id"], revision=1, description="first").status_code == 200

    stale = patch(owner, workspace, agent["id"], revision=1, description="second")

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "AGENT_REVISION_CONFLICT"
    assert stale.json()["error"]["details"] == {"current_revision": 2}
    assert fetch(owner, workspace, agent["id"])["description"] == "first"


def test_rename_allows_a_case_change_but_not_another_agents_name(
    owner: TestClient, workspace: str
) -> None:
    agent = create(owner, workspace, "Summarizer").json()
    create(owner, workspace, "Translator")

    recased = patch(owner, workspace, agent["id"], revision=1, name="SUMMARIZER")
    taken = patch(owner, workspace, agent["id"], revision=2, name="translator")

    assert recased.status_code == 200 and recased.json()["name"] == "SUMMARIZER"
    assert taken.status_code == 409
    assert taken.json()["error"]["code"] == "AGENT_NAME_TAKEN"


def test_duplicate_name_in_workspace_conflicts(owner: TestClient, workspace: str) -> None:
    create(owner, workspace, "Summarizer")

    response = create(owner, workspace, "summarizer")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AGENT_NAME_TAKEN"


# --- validation -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    [
        {**CONFIG, "tools": [{"tool": "shell"}]},
        {**CONFIG, "capabilities": ["root"]},
        {**CONFIG, "is_admin": True},
        {**CONFIG, "model": {"credential_ref": "sk-" + "z" * 40}},
    ],
)
def test_invalid_configuration_is_rejected(
    owner: TestClient, workspace: str, config: dict[str, Any]
) -> None:
    response = create(owner, workspace, config=config)

    assert response.status_code == 422
    assert "sk-zzzz" not in response.text


# --- RBAC -----------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["MEMBER", "VIEWER"])
def test_members_and_viewers_cannot_manage_agents(
    new_client: ClientFactory, owner: TestClient, workspace: str, role: str
) -> None:
    agent = create(owner, workspace).json()
    actor = member(new_client, owner, role)

    responses = [
        create(actor, workspace, "Other"),
        patch(actor, workspace, agent["id"], revision=agent["revision"], description="x"),
        act(actor, workspace, agent, "publish"),
        act(actor, workspace, agent, "activate"),
    ]

    for response in responses:
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "INSUFFICIENT_ROLE"
    assert actor.get(agents_url(workspace, f"/{agent['id']}")).status_code == 200


def test_only_owners_can_make_code_execution_tools_effective(
    new_client: ClientFactory, owner: TestClient, workspace: str
) -> None:
    admin = member(new_client, owner, "ADMIN")
    agent = create(admin, workspace, config=HIGH_RISK_CONFIG).json()

    by_admin = act(admin, workspace, agent, "publish")
    assert by_admin.status_code == 403
    assert by_admin.json()["error"]["code"] == "TOOL_REQUIRES_OWNER"

    assert act(owner, workspace, agent, "publish").status_code == 201
    agent = fetch(owner, workspace, agent["id"])
    assert act(admin, workspace, agent, "activate").json()["error"]["code"] == "TOOL_REQUIRES_OWNER"
    assert act(owner, workspace, agent, "activate").status_code == 200


def test_admin_can_manage_low_risk_agents(
    new_client: ClientFactory, owner: TestClient, workspace: str
) -> None:
    admin = member(new_client, owner, "ADMIN")
    agent = create(admin, workspace).json()

    assert act(admin, workspace, agent, "publish").status_code == 201
    assert (
        act(admin, workspace, fetch(admin, workspace, agent["id"]), "activate").status_code == 200
    )


# --- workspace isolation ----------------------------------------------------------------------


def test_agents_are_invisible_and_untouchable_across_workspaces(
    new_client: ClientFactory, owner: TestClient, workspace: str
) -> None:
    agent = create(owner, workspace).json()
    mallory = new_client()
    register(mallory, new_email())
    theirs = my_workspace(mallory)

    via_own_workspace = [
        mallory.get(agents_url(theirs, f"/{agent['id']}")),
        mallory.get(agents_url(theirs, f"/{agent['id']}/versions")),
        patch(mallory, theirs, agent["id"], revision=1, description="pwned"),
        act(mallory, theirs, agent, "publish"),
        mallory.get(agents_url(theirs, f"/{uuid.uuid4()}")),
    ]
    via_victim_workspace = mallory.get(agents_url(workspace, f"/{agent['id']}"))

    for response in via_own_workspace:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "AGENT_NOT_FOUND"
    assert via_victim_workspace.status_code == 404
    assert via_victim_workspace.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"
    listed = {a["id"] for a in mallory.get(agents_url(theirs)).json()["items"]}
    assert agent["id"] not in listed


# --- proposals ------------------------------------------------------------------------------


def test_member_proposal_approved_by_admin_registers_an_inactive_agent(
    new_client: ClientFactory, owner: TestClient, workspace: str
) -> None:
    proposer = member(new_client, owner, "MEMBER")
    admin = member(new_client, owner, "ADMIN")
    proposal = propose(proposer, workspace).json()
    assert proposal["status"] == "PENDING" and proposal["agent_id"] is None

    approved = decide(admin, workspace, proposal["id"], "approve")

    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "APPROVED" and body["reviewed_by"] is not None
    agent = fetch(owner, workspace, body["agent_id"])
    assert agent["status"] == "INACTIVE"  # approval is not activation
    assert agent["active_version"] == 1
    version = owner.get(agents_url(workspace, f"/{agent['id']}/versions")).json()["items"][0]
    assert version["configuration"] == agent["draft_configuration"]
    assert version["configuration"]["tools"] == CONFIG["tools"]
    assert act(admin, workspace, agent, "activate").status_code == 200
    again = decide(admin, workspace, proposal["id"], "approve")
    assert again.status_code == 409 and again.json()["error"]["code"] == "PROPOSAL_ALREADY_DECIDED"


def test_rejected_proposal_creates_nothing(
    new_client: ClientFactory, owner: TestClient, workspace: str
) -> None:
    proposer = member(new_client, owner, "MEMBER")
    before = owner.get(agents_url(workspace)).json()["total"]
    proposal = propose(proposer, workspace).json()

    rejected = decide(owner, workspace, proposal["id"], "reject")

    assert rejected.json()["status"] == "REJECTED"
    assert rejected.json()["agent_id"] is None
    assert owner.get(agents_url(workspace)).json()["total"] == before
    pending = owner.get(
        f"{API}/workspaces/{workspace}/agent-proposals", params={"status": "PENDING"}
    )
    assert pending.json()["total"] == 0


def test_proposer_cannot_review_their_own_proposal(owner: TestClient, workspace: str) -> None:
    proposal = propose(owner, workspace).json()

    for decision in ("approve", "reject"):
        response = decide(owner, workspace, proposal["id"], decision)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PROPOSAL_SELF_REVIEW"


def test_proposal_permissions(new_client: ClientFactory, owner: TestClient, workspace: str) -> None:
    viewer = member(new_client, owner, "VIEWER")
    proposer = member(new_client, owner, "MEMBER")
    other_member = member(new_client, owner, "MEMBER")
    proposal = propose(proposer, workspace).json()

    assert propose(viewer, workspace).json()["error"]["code"] == "INSUFFICIENT_ROLE"
    unauthorized = decide(other_member, workspace, proposal["id"], "approve")
    assert unauthorized.status_code == 403
    assert unauthorized.json()["error"]["code"] == "INSUFFICIENT_ROLE"


def test_high_risk_proposal_needs_an_owner_to_approve(
    new_client: ClientFactory, owner: TestClient, workspace: str
) -> None:
    admin = member(new_client, owner, "ADMIN")
    proposer = member(new_client, owner, "MEMBER")
    proposal = propose(proposer, workspace, HIGH_RISK_CONFIG).json()

    assert decide(admin, workspace, proposal["id"], "approve").json()["error"]["code"] == (
        "TOOL_REQUIRES_OWNER"
    )
    assert decide(owner, workspace, proposal["id"], "approve").status_code == 200


@pytest.mark.parametrize(
    "extra",
    [{"status": "APPROVED"}, {"reviewed_by": str(uuid.uuid4())}, {"agent_id": str(uuid.uuid4())}],
)
def test_proposal_cannot_set_its_own_decision_fields(
    owner: TestClient, workspace: str, extra: dict[str, str]
) -> None:
    assert propose(owner, workspace, CONFIG, **extra).status_code == 422


def test_proposed_content_is_frozen_in_the_database(
    new_client: ClientFactory, owner: TestClient, workspace: str, api_settings: Settings
) -> None:
    proposer = member(new_client, owner, "MEMBER")
    proposal = propose(proposer, workspace).json()

    with pytest.raises(DBAPIError, match="cannot change"):
        sql(
            api_settings,
            "UPDATE agent_proposals SET proposed_configuration = CAST(:escalated AS jsonb) "
            "WHERE id = :id",
            escalated='{"tools": ["test_runner"]}',
            id=proposal["id"],
        )
    decide(owner, workspace, proposal["id"], "reject")
    with pytest.raises(DBAPIError, match="already been decided"):
        sql(
            api_settings,
            "UPDATE agent_proposals SET status = 'PENDING' WHERE id = :id",
            id=proposal["id"],
        )


def test_proposals_are_isolated_per_workspace(
    new_client: ClientFactory, owner: TestClient, workspace: str
) -> None:
    proposal = propose(owner, workspace).json()
    mallory = new_client()
    register(mallory, new_email())
    theirs = my_workspace(mallory)

    response = decide(mallory, theirs, proposal["id"], "approve")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROPOSAL_NOT_FOUND"
    assert mallory.get(f"{API}/workspaces/{theirs}/agent-proposals").json()["total"] == 0


# --- audit and pagination -------------------------------------------------------------------


def test_every_registry_change_is_audited(
    owner: TestClient, workspace: str, api_settings: Settings
) -> None:
    agent = create(owner, workspace).json()
    agent = patch(
        owner, workspace, agent["id"], revision=agent["revision"], description="v2"
    ).json()
    act(owner, workspace, agent, "publish")
    agent = fetch(owner, workspace, agent["id"])
    act(owner, workspace, agent, "activate")
    act(owner, workspace, fetch(owner, workspace, agent["id"]), "deactivate")
    user_id = owner.get(f"{API}/auth/me").json()["user"]["id"]

    rows = sql(
        api_settings,
        "SELECT action, actor_user_id::text, workspace_id::text FROM agent_audit_events "
        "WHERE agent_id = :id ORDER BY created_at, action",
        id=agent["id"],
    )

    assert [r[0] for r in rows] == [
        "CREATED",
        "DRAFT_UPDATED",
        "VERSION_PUBLISHED",
        "ACTIVATED",
        "DEACTIVATED",
    ]
    assert {(r[1], r[2]) for r in rows} == {(user_id, workspace)}
    with pytest.raises(DBAPIError, match="append-only"):
        sql(api_settings, "UPDATE agent_audit_events SET action = 'CREATED'")


def test_agent_and_version_lists_are_paginated(owner: TestClient, workspace: str) -> None:
    page = owner.get(agents_url(workspace), params={"limit": 2, "offset": 0}).json()
    filtered = owner.get(agents_url(workspace), params={"status": "INACTIVE"}).json()

    assert page["total"] == 6 and len(page["items"]) == 2
    assert filtered["total"] == 0
    assert owner.get(agents_url(workspace), params={"limit": 0}).status_code == 422
