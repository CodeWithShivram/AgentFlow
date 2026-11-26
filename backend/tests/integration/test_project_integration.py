"""The integration gate (Step 9.5, ADR-0051): a software project completes only after its
profile's checks pass in the sandbox on the composed workspace; failures and an unavailable
sandbox block it, and resuming retries on the current tree."""

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.sync_session import session_scope
from app.execution import outbox
from app.models import (
    IntegrationStatus,
    OutboxEvent,
    ProjectIntegration,
    Workflow,
    WorkflowStatus,
)
from app.projects import integration_job
from app.projects.workspace import ProjectWorkspaces
from app.sandbox.runner import DockerSandbox, SandboxResult, SandboxUnavailableError
from app.workflows import engine
from app.workflows.engine import TransitionPreconditionError
from app.workflows.state_machine import Actor
from tests.integration.api_helpers import API, csrf, my_workspace, new_email, register
from tests.integration.test_project_workspace import (
    SCOPES,
    _apply,
    _events,
    _run_task,
    _start_project,
)
from tests.integration.test_sandbox import _image_ready

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]


class _FakeSandbox(DockerSandbox):
    def __init__(self, *, succeed: bool = True, unavailable: bool = False) -> None:
        super().__init__()
        self.succeed, self.unavailable, self.calls = succeed, unavailable, 0

    def run(self, image: str, project: Path, command: list[str]) -> SandboxResult:  # type: ignore[override]
        self.calls += 1
        if self.unavailable:
            raise SandboxUnavailableError("DOCKER_UNAVAILABLE")
        return SandboxResult(
            exit_code=0 if self.succeed else 1,
            timed_out=False,
            output="ok" if self.succeed else "1 failed",
            duration_ms=5,
        )


def _built_and_applied(admin: Factory, worker: Factory, workspaces: ProjectWorkspaces):  # type: ignore[no-untyped-def]
    graph = _start_project(admin)
    for index in range(len(SCOPES)):
        _run_task(admin, worker, graph, index)
    for event in _events(admin, graph, outbox.PROJECT_APPLY):
        assert _apply(worker, workspaces, event) == "applied"
    return graph


def _integrate(
    worker: Factory, workspaces: ProjectWorkspaces, sandbox: DockerSandbox, event: OutboxEvent
) -> str:
    with session_scope(worker) as s:
        job = integration_job.claim(
            s,
            workspaces,
            event_id=event.id,
            workspace_id=event.workspace_id,
            workflow_id=uuid.UUID(event.payload["workflow_id"]),
        )
    if isinstance(job, str):
        return job
    outcome = integration_job.execute(sandbox, job)
    with session_scope(worker) as s:
        return integration_job.finish(s, workspaces, job, outcome)


def _workflow(admin: Factory, workflow_id: uuid.UUID) -> Workflow:
    with session_scope(admin) as s:
        workflow = s.get(Workflow, workflow_id)
        assert workflow is not None
        return workflow


def _integrations(admin: Factory) -> list[ProjectIntegration]:
    with session_scope(admin) as s:
        return list(s.scalars(select(ProjectIntegration).order_by(ProjectIntegration.created_at)))


def test_requests_before_the_last_apply_wait_and_the_last_apply_requests_again(
    admin_factory: Factory, worker_factory: Factory, tmp_path: Path
) -> None:
    workspaces = ProjectWorkspaces(tmp_path / "w")
    graph = _start_project(admin_factory)
    for index in range(len(SCOPES)):
        _run_task(admin_factory, worker_factory, graph, index)
    # All tasks are COMPLETED, but the workflow waits for the gate instead of completing.
    assert _workflow(admin_factory, graph.workflow_id).status == WorkflowStatus.RUNNING
    early = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)
    assert early and _integrate(worker_factory, workspaces, _FakeSandbox(), early[-1]) == "waiting"

    for event in _events(admin_factory, graph, outbox.PROJECT_APPLY):
        _apply(worker_factory, workspaces, event)
    latest = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)[-1]
    assert latest.id != early[-1].id
    assert _integrate(worker_factory, workspaces, _FakeSandbox(), latest) == "passed"
    assert _workflow(admin_factory, graph.workflow_id).status == WorkflowStatus.COMPLETED


@pytest.mark.skipif(
    not _image_ready("agentflow-sandbox/python-3.12:1"), reason="python sandbox image missing"
)
def test_the_mock_project_passes_its_real_sandbox_checks(
    admin_factory: Factory, worker_factory: Factory, tmp_path: Path
) -> None:
    workspaces = ProjectWorkspaces(tmp_path / "w")
    graph = _built_and_applied(admin_factory, worker_factory, workspaces)
    event = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)[-1]

    assert _integrate(worker_factory, workspaces, DockerSandbox(), event) == "passed"

    (run,) = _integrations(admin_factory)
    assert run.status == IntegrationStatus.PASSED and run.finished_at is not None
    assert [(c["name"], c["outcome"]) for c in run.checks] == [
        ("compile", "passed"),
        ("tests", "passed"),
    ]
    assert "1 passed" in run.checks[1]["output"]
    assert _workflow(admin_factory, graph.workflow_id).status == WorkflowStatus.COMPLETED


def test_failed_checks_block_and_resuming_retries(
    admin_factory: Factory, worker_factory: Factory, tmp_path: Path
) -> None:
    workspaces = ProjectWorkspaces(tmp_path / "w")
    graph = _built_and_applied(admin_factory, worker_factory, workspaces)
    event = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)[-1]

    assert _integrate(worker_factory, workspaces, _FakeSandbox(succeed=False), event) == "failed"
    assert _workflow(admin_factory, graph.workflow_id).status == WorkflowStatus.BLOCKED
    assert _integrations(admin_factory)[-1].error_code == "CHECKS_FAILED"

    with session_scope(admin_factory) as s:
        workflow = s.get(Workflow, graph.workflow_id)
        assert workflow is not None
        engine.transition_workflow(s, workflow, WorkflowStatus.RUNNING, actor=Actor.USER)
        s.commit()
    retry = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)[-1]
    assert retry.id != event.id
    assert _integrate(worker_factory, workspaces, _FakeSandbox(), retry) == "passed"
    assert [r.status for r in _integrations(admin_factory)] == [
        IntegrationStatus.FAILED,
        IntegrationStatus.PASSED,
    ]
    assert _workflow(admin_factory, graph.workflow_id).status == WorkflowStatus.COMPLETED


def test_an_unavailable_sandbox_is_never_a_pass(
    admin_factory: Factory, worker_factory: Factory, tmp_path: Path
) -> None:
    workspaces = ProjectWorkspaces(tmp_path / "w")
    graph = _built_and_applied(admin_factory, worker_factory, workspaces)
    event = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)[-1]

    assert (
        _integrate(worker_factory, workspaces, _FakeSandbox(unavailable=True), event) == "not_run"
    )
    run = _integrations(admin_factory)[-1]
    assert (run.status, run.error_code) == (IntegrationStatus.NOT_RUN, "DOCKER_UNAVAILABLE")
    assert _workflow(admin_factory, graph.workflow_id).status == WorkflowStatus.BLOCKED


def test_a_software_project_cannot_complete_without_a_passed_integration(
    admin_factory: Factory, worker_factory: Factory, tmp_path: Path
) -> None:
    workspaces = ProjectWorkspaces(tmp_path / "w")
    graph = _built_and_applied(admin_factory, worker_factory, workspaces)
    with session_scope(admin_factory) as s:
        workflow = s.get(Workflow, graph.workflow_id)
        assert workflow is not None
        with pytest.raises(TransitionPreconditionError):
            engine.transition_workflow(s, workflow, WorkflowStatus.COMPLETED, actor=Actor.SYSTEM)


def test_duplicate_and_concurrent_requests_run_the_gate_once(
    admin_factory: Factory, worker_factory: Factory, tmp_path: Path
) -> None:
    workspaces = ProjectWorkspaces(tmp_path / "w")
    graph = _built_and_applied(admin_factory, worker_factory, workspaces)
    requests = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)
    event = requests[-1]
    with session_scope(worker_factory) as s:
        job = integration_job.claim(
            s, workspaces, event_id=event.id, workspace_id=event.workspace_id,
            workflow_id=graph.workflow_id,
        )  # fmt: skip
    assert isinstance(job, integration_job.IntegrationJob)
    # While it runs, other requests (and redelivery of this one) change nothing.
    assert _integrate(worker_factory, workspaces, _FakeSandbox(), event) == "duplicate"
    for other in requests[:-1]:
        assert _integrate(worker_factory, workspaces, _FakeSandbox(), other) in {
            "running",
            "duplicate",
        }
    with session_scope(worker_factory) as s:
        outcome = integration_job.execute(_FakeSandbox(), job)
        assert integration_job.finish(s, workspaces, job, outcome) == "passed"
    assert len(_integrations(admin_factory)) == 1


def test_members_read_runs_and_applications_and_strangers_get_404(new_client: Any) -> None:
    owner, stranger = new_client(), new_client()
    for client in (owner, stranger):
        assert register(client, new_email()).status_code == 201
    base = f"{API}/workspaces/{my_workspace(owner)}/workflows"
    created = owner.post(
        base, json={"prompt": "Build a CLI.", "kind": "SOFTWARE_PROJECT"}, headers=csrf(owner)
    ).json()
    for suffix in ("/integrations", "/workspace-applications"):
        assert owner.get(f"{base}/{created['id']}{suffix}").json() == []
        foreign = f"{API}/workspaces/{my_workspace(stranger)}/workflows/{created['id']}{suffix}"
        assert stranger.get(foreign).status_code == 404
        assert stranger.get(f"{base}/{created['id']}{suffix}").status_code == 404
