"""Software project workflows (Step 9.2, ADR-0048): the kind, version 2 plan drafts with
stack profiles and file ownership, kind checks in the planner and at confirmation."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.execution import outbox
from app.llm.mock import MockModelAdapter
from app.llm.types import ChatMessage, ModelRequest
from app.planning import plan
from tests.integration.api_helpers import ClientFactory, csrf, new_email, register
from tests.integration.test_plan_generation import (
    _confirm,
    _requests,
    _tasks,
    _url,
    _workflow,
)

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
Jobs = dict[str, Callable[..., str]]
PROMPT = "Build a command-line tool that counts words in text files, with tests."


@pytest.fixture
def owner(new_client: ClientFactory) -> TestClient:
    client = new_client()
    assert register(client, new_email()).status_code == 201
    return client


def _analysed(client: TestClient, admin: Factory, jobs: Jobs, kind: str) -> str:
    created = client.post(
        _url(client), json={"prompt": PROMPT, "kind": kind}, headers=csrf(client)
    ).json()
    assert created["kind"] == kind
    client.post(
        _url(client, created["id"], "/transitions"),
        json={"to_status": "PLANNING", "revision": created["revision"]},
        headers=csrf(client),
    )
    (request,) = _requests(admin, created["id"], outbox.WORKFLOW_ANALYZE)
    assert jobs["analysis"](request) == "ready"
    workflow_id: str = created["id"]
    return workflow_id


def _plan_request(admin: Factory, workflow_id: str) -> Any:
    return _requests(admin, workflow_id, outbox.WORKFLOW_PLAN)[-1]


def test_a_software_project_is_planned_and_confirmed_with_ownership(
    owner: TestClient, admin_factory: Factory, jobs: Jobs, seeded_builtins: Settings
) -> None:
    workflow_id = _analysed(owner, admin_factory, jobs, "SOFTWARE_PROJECT")
    adapter = MockModelAdapter()
    assert jobs["plan"](_plan_request(admin_factory, workflow_id), adapter) == "ready"

    system = adapter.requests[-1].messages[0].content
    assert "SOFTWARE PROJECT" in system
    assert "<stack_profiles>" in adapter.requests[-1].messages[-1].content
    draft = owner.get(_url(owner, workflow_id, "/plan-drafts/1")).json()
    assert draft["plan"]["schema_version"] == 2
    assert draft["plan"]["project"]["stack_profile"] == "python-3.12"
    events = owner.get(_url(owner, workflow_id, "/events")).json()["items"]
    generated = [e for e in events if e["event_type"] == "PLAN_GENERATED"][-1]
    assert generated["payload"]["prompt_version"] == plan.SOFTWARE_PROMPT_VERSION

    response = _confirm(owner, workflow_id)
    assert response.status_code == 200, response.json()
    assert response.json()["project_spec"]["stack_profile"] == "python-3.12"
    scopes = {t["title"]: (t["file_scope"], t["effort_points"]) for t in _tasks(owner, workflow_id)}
    assert scopes["Set up the project"] == (["pyproject.toml", "README.md", ".env.example"], 2)
    assert scopes["Implement the application"] == (["src/"], 5)
    assert scopes["Write the automated tests"] == (["tests/"], 3)
    # The platform pre-assigned every task to the top-ranked eligible agent (ADR-0054).
    tasks = _tasks(owner, workflow_id)
    assert all(t["assigned_agent_id"] and t["status"] == "PENDING" for t in tasks)
    events = owner.get(_url(owner, workflow_id, "/events")).json()["items"]
    automatic = [e for e in events if e["event_type"] == "TASK_ASSIGNED"]
    assert len(automatic) == 3 and all(e["payload"]["automatic"] for e in automatic)


def test_the_planner_retries_plans_of_the_wrong_kind(
    owner: TestClient, admin_factory: Factory, jobs: Jobs
) -> None:
    software = _analysed(owner, admin_factory, jobs, "SOFTWARE_PROJECT")
    general_reply = plan.mock_plan(_general_request())
    adapter = MockModelAdapter(defaults={})
    adapter.script(plan.PURPOSE, *[general_reply] * plan.MAX_ATTEMPTS)
    assert jobs["plan"](_plan_request(admin_factory, software), adapter) == "failed"
    last = owner.get(_url(owner, software, "/events")).json()["items"][-1]
    assert last["payload"]["error_code"] == "PLAN_PROJECT_MISSING"

    general = _analysed(owner, admin_factory, jobs, "GENERAL")
    project_reply = plan.mock_software_plan(plan._profiles_section(None))
    adapter = MockModelAdapter(defaults={})
    adapter.script(plan.PURPOSE, *[project_reply] * plan.MAX_ATTEMPTS)
    assert jobs["plan"](_plan_request(admin_factory, general), adapter) == "failed"
    last = owner.get(_url(owner, general, "/events")).json()["items"][-1]
    assert last["payload"]["error_code"] == "PLAN_PROJECT_UNEXPECTED"


def _general_request() -> Any:
    analysis = plan._delimited("analysis", json.dumps({"requirements": [{"description": "x"}]}))
    return ModelRequest(
        purpose=plan.PURPOSE,
        messages=[
            ChatMessage(role="system", content="s"),
            ChatMessage(role="user", content=analysis),
        ],
    )


def test_appending_a_plan_that_owns_the_same_files_is_refused(
    owner: TestClient, admin_factory: Factory, jobs: Jobs, seeded_builtins: Settings
) -> None:
    workflow_id = _analysed(owner, admin_factory, jobs, "SOFTWARE_PROJECT")
    assert jobs["plan"](_plan_request(admin_factory, workflow_id)) == "ready"
    assert _confirm(owner, workflow_id).status_code == 200
    assert (
        owner.post(_url(owner, workflow_id, "/plan-drafts"), headers=csrf(owner)).status_code == 200
    )
    assert jobs["plan"](_plan_request(admin_factory, workflow_id)) == "ready"

    appended = _confirm(owner, workflow_id, version=2, existing_tasks="append")
    assert appended.status_code == 409
    assert appended.json()["error"]["code"] == "PLAN_DRAFT_INVALID"
    assert "FILE_SCOPE_OVERLAP" in appended.json()["error"]["details"]["codes"]

    replaced = _confirm(owner, workflow_id, version=2, existing_tasks="replace")
    assert replaced.status_code == 200
    active = [t for t in _tasks(owner, workflow_id) if not t["removed"]]
    assert len(active) == 3


def test_general_workflows_are_unchanged(
    owner: TestClient, admin_factory: Factory, jobs: Jobs, seeded_builtins: Settings
) -> None:
    workflow_id = _analysed(owner, admin_factory, jobs, "GENERAL")
    assert jobs["plan"](_plan_request(admin_factory, workflow_id)) == "ready"
    assert _confirm(owner, workflow_id, acknowledge_warnings=True).status_code == 200
    body = _workflow(owner, workflow_id)
    assert body["kind"] == "GENERAL"
    assert body["project_spec"] is None
    assert all(
        t["file_scope"] == [] and t["effort_points"] is None for t in _tasks(owner, workflow_id)
    )
