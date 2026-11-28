"""Change projects (Step 9.12, ADR-0059): a completed software project is changed by a new
project that starts from its verified commit, plans only the change, and keeps the base intact."""

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.sync_session import session_scope
from app.execution import outbox
from app.llm.mock import MockModelAdapter
from app.models import (
    IntegrationStatus,
    OutboxEvent,
    ProjectIntegration,
    Workflow,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowPlan,
    WorkflowStatus,
    WorkflowTask,
)
from app.planning import plan
from app.projects.context import fix_context
from app.projects.workspace import ProjectWorkspaces
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
SPEC = {"name": "tool", "stack_profile": "python-3.12", "architecture": "One module."}
BASE_FILES = {
    "pyproject.toml": '[project]\nname = "tool"\nversion = "0.1.0"\n',
    "README.md": "# Tool\n",
    "src/tool/__init__.py": "",
    "src/tool/core.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
    "tests/test_core.py": (
        "from tool.core import add\n\n\ndef test_add() -> None:\n    assert add(1, 2) == 3\n"
    ),
}


@pytest.fixture
def owner(new_client: ClientFactory) -> TestClient:
    client = new_client()
    assert register(client, new_email()).status_code == 201
    return client


def _base_url(client: TestClient) -> str:
    return f"{API}/workspaces/{my_workspace(client)}/workflows"


def _completed_base(
    client: TestClient, admin: Factory, workspaces: ProjectWorkspaces
) -> tuple[dict[str, Any], str]:
    """A software project made COMPLETED with a workspace and a PASSED integration, as the gate
    leaves a verified project. Returns the project and its verified commit."""
    base = client.post(
        _base_url(client), json={"prompt": "Build a tool.", "kind": "SOFTWARE_PROJECT"},
        headers=csrf(client),
    ).json()  # fmt: skip
    workspace_id, workflow_id = uuid.UUID(base["workspace_id"]), uuid.UUID(base["id"])
    for scope in (["pyproject.toml", "README.md"], ["src/"], ["tests/"]):
        files = {
            p: c for p, c in BASE_FILES.items() if any(p == e or p.startswith(e) for e in scope)
        }
        workspaces.apply(workspace_id, workflow_id, scope=scope, files=files, message="base")
    head = workspaces.head(workspaces.project_path(workspace_id, workflow_id))
    with session_scope(admin) as s:
        row = s.get(Workflow, workflow_id)
        assert row is not None
        now = datetime.now(UTC)
        row.status, row.project_spec = WorkflowStatus.COMPLETED, SPEC
        row.started_at, row.completed_at = now, now
        s.add(
            ProjectIntegration(
                workspace_id=workspace_id, workflow_id=workflow_id, head_sha=head,
                stack_profile="python-3.12", status=IntegrationStatus.PASSED, checks=[],
                finished_at=now,
            )
        )  # fmt: skip
        s.commit()
    return client.get(f"{_base_url(client)}/{base['id']}").json(), head


def _events(admin: Factory, workflow_id: str, event_type: str) -> list[OutboxEvent]:
    with session_scope(admin) as s:
        return list(
            s.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.workflow_id == uuid.UUID(workflow_id),
                    OutboxEvent.event_type == event_type,
                )
                .order_by(OutboxEvent.created_at)
            )
        )


def _change(client: TestClient, base: dict[str, Any], prompt: str, **headers: str) -> Any:
    return client.post(
        f"{_base_url(client)}/{base['id']}/changes",
        json={"prompt": prompt},
        headers={**csrf(client), **headers},
    )


def test_only_a_verified_completed_project_can_be_changed(
    owner: TestClient, new_client: ClientFactory, admin_factory: Factory, tmp_path: Path
) -> None:
    early = owner.post(
        _base_url(owner), json={"prompt": "Build a CLI.", "kind": "SOFTWARE_PROJECT"},
        headers=csrf(owner),
    ).json()  # fmt: skip
    refused = _change(owner, early, "Add a flag.")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "CHANGE_NOT_AVAILABLE"
    general = owner.post(_base_url(owner), json={"prompt": "Write a memo."}, headers=csrf(owner))
    assert _change(owner, general.json(), "Shorter.").status_code == 409

    base, head = _completed_base(owner, admin_factory, ProjectWorkspaces(tmp_path / "w"))
    viewer = member(new_client, owner, "VIEWER")
    assert _change(viewer, base, "Add a flag.").status_code == 403
    assert _change(owner, base, "   ").status_code == 422

    created = _change(owner, base, "Add a subtract function.", **{"Idempotency-Key": "chg-1"})
    assert created.status_code == 201
    change = created.json()
    assert (change["kind"], change["status"]) == ("SOFTWARE_PROJECT", "PENDING")
    assert (change["base_workflow_id"], change["base_head_sha"]) == (base["id"], head)
    assert change["project_spec"] == SPEC and change["prompt"] == "Add a subtract function."
    assert change["autonomy_policy"] == base["autonomy_policy"]
    replay = _change(owner, base, "Add a subtract function.", **{"Idempotency-Key": "chg-1"})
    assert replay.headers.get("Idempotent-Replayed") == "true"
    assert replay.json()["id"] == change["id"]
    with session_scope(admin_factory) as s:
        (created_event,) = s.scalars(
            select(WorkflowEvent).where(WorkflowEvent.workflow_id == uuid.UUID(change["id"]))
        )
        assert created_event.event_type == WorkflowEventType.WORKFLOW_CREATED
        assert created_event.payload["base_workflow_id"] == base["id"]

    # A deleted base is gone for the API, so it cannot be changed either.
    assert (
        owner.delete(
            f"{_base_url(owner)}/{base['id']}",
            params={"revision": base["revision"]},
            headers=csrf(owner),
        ).status_code
        == 204
    )
    assert _change(owner, base, "Again.").status_code == 404


def test_a_change_starts_from_the_verified_code_and_plans_only_the_change(
    owner: TestClient,
    admin_factory: Factory,
    jobs: dict[str, Callable[..., str]],
    tmp_path: Path,
) -> None:
    workspaces = ProjectWorkspaces(tmp_path / "w")
    base, head = _completed_base(owner, admin_factory, workspaces)
    change = _change(owner, base, "Add a subtract function.").json()
    url = f"{_base_url(owner)}/{change['id']}"
    owner.post(
        f"{url}/transitions",
        json={"to_status": "PLANNING", "revision": change["revision"]},
        headers=csrf(owner),
    )
    (analysis,) = _events(admin_factory, change["id"], outbox.WORKFLOW_ANALYZE)
    assert jobs["analysis"](analysis) == "ready"
    (request,) = _events(admin_factory, change["id"], outbox.WORKFLOW_PLAN)

    adapter = MockModelAdapter()
    assert jobs["plan"](request, adapter, workspaces) == "ready"

    # The new workspace starts as the base's files at the verified commit.
    workspace_id = uuid.UUID(change["workspace_id"])
    project = workspaces.project_path(workspace_id, uuid.UUID(change["id"]))
    assert workspaces.read_scope(project, None, max_files=100, max_chars=10**6) == BASE_FILES
    # The planner saw the current project, on the base's profile only.
    (text,) = [r.messages[-1].content for r in adapter.requests if r.purpose == plan.PURPOSE]
    current = json.loads(text.split("<current_project>\n", 1)[1].split("\n</current_project>")[0])
    assert current["paths"] == sorted(BASE_FILES) and current["files_left_out"] == []
    assert current["stack_profile"] == "python-3.12"
    assert [p["id"] for p in json.loads(text.split("<stack_profiles>\n")[1].split("\n<")[0])] == [
        "python-3.12"
    ]
    with session_scope(admin_factory) as s:
        (draft,) = s.scalars(
            select(WorkflowPlan).where(WorkflowPlan.workflow_id == uuid.UUID(change["id"]))
        )
        assert draft.prompt_version == plan.CHANGE_PROMPT_VERSION
        assert [t["file_scope"] for t in draft.plan["tasks"]] == [["src/change/"], ["README.md"]]

    # Shared files stay with the base's copy: confirming needs no owner for pyproject.toml.
    workflow = owner.get(url).json()
    confirmed = owner.post(
        f"{url}/plan-drafts/1/confirm",
        json={"revision": workflow["revision"], "acknowledge_warnings": True},
        headers=csrf(owner),
    )
    assert confirmed.status_code == 200, confirmed.text

    # A change task sees its scope's current files and the rest of the project.
    with session_scope(admin_factory) as s:
        row = s.get(Workflow, uuid.UUID(change["id"]))
        assert row is not None
        readme = s.scalars(
            select(WorkflowTask).where(
                WorkflowTask.workflow_id == row.id, WorkflowTask.title == "Document the change"
            )
        ).one()
        feedback, current_files, others = fix_context(s, row, readme, workspaces)
    assert feedback == []
    assert current_files == [{"path": "README.md", "content": "# Tool\n"}]
    assert {f["path"] for f in others} == set(BASE_FILES) - {"README.md"}
    # The base project is untouched.
    base_project = workspaces.project_path(workspace_id, uuid.UUID(base["id"]))
    assert workspaces.head(base_project) == head


def test_a_base_that_moved_fails_the_plan_instead_of_guessing(
    owner: TestClient,
    admin_factory: Factory,
    jobs: dict[str, Callable[..., str]],
    tmp_path: Path,
) -> None:
    workspaces = ProjectWorkspaces(tmp_path / "w")
    base, _ = _completed_base(owner, admin_factory, workspaces)
    change = _change(owner, base, "Add a flag.").json()
    workspace_id = uuid.UUID(base["workspace_id"])
    workspaces.apply(
        workspace_id, uuid.UUID(base["id"]), scope=["README.md"], files={"README.md": "# Moved\n"},
        message="moved",
    )  # fmt: skip
    owner.post(
        f"{_base_url(owner)}/{change['id']}/transitions",
        json={"to_status": "PLANNING", "revision": change["revision"]},
        headers=csrf(owner),
    )
    (analysis,) = _events(admin_factory, change["id"], outbox.WORKFLOW_ANALYZE)
    jobs["analysis"](analysis)
    (request,) = _events(admin_factory, change["id"], outbox.WORKFLOW_PLAN)
    assert jobs["plan"](request, None, workspaces) == "failed"
    assert owner.get(f"{_base_url(owner)}/{change['id']}").json()["plan_status"] == "FAILED"
    with session_scope(admin_factory) as s:
        failed = s.scalars(
            select(WorkflowEvent).where(
                WorkflowEvent.workflow_id == uuid.UUID(change["id"]),
                WorkflowEvent.event_type == WorkflowEventType.PLAN_GENERATION_FAILED,
            )
        ).one()
        assert failed.payload["error_code"] == "BASE_CHANGED"
