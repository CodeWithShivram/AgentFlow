"""Project workspaces end to end (Step 9.3, ADR-0049): software project tasks run with the real
Developer runtime on the mock model, pass platform verification with their scope, and the apply
job writes each accepted task into the project's Git workspace exactly once."""

import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.sync_session import session_scope
from app.execution import outbox, runner
from app.llm.gateway import ModelGateway
from app.llm.mock import MockModelAdapter
from app.models import (
    Agent,
    AgentStatus,
    AgentVersion,
    AutonomyPolicy,
    OutboxEvent,
    TaskDependency,
    TaskStatus,
    User,
    Workflow,
    WorkflowKind,
    WorkflowStatus,
    WorkflowTask,
    Workspace,
    WorkspaceApplication,
    WorkspaceApplicationStatus,
)
from app.projects import apply_job
from app.projects.workspace import ProjectWorkspaces
from app.schemas.agents import AgentConfiguration
from app.workflows import engine
from app.workflows.state_machine import Actor
from tests.conftest import make_settings
from tests.integration.test_execution_scenarios import (
    Graph,
    _claim,
    _drop_side_requests,
    _verify,
)

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
DEVELOPER_CONFIG = {
    "schema_version": 1,
    "system_instruction_ref": "builtin://developer/v1",
    "capabilities": ["code_generation"],
    "tools": [],
}
SCOPES = [["pyproject.toml", "README.md"], ["src/"], ["tests/"]]
_MODELS = ModelGateway(make_settings(), MockModelAdapter(), lambda record: uuid.uuid4())


def _start_project(admin: Factory, kind: WorkflowKind = WorkflowKind.SOFTWARE_PROJECT) -> Graph:
    """A running software project: setup -> application -> tests, all pre-assigned to a
    Developer agent version."""
    with session_scope(admin) as s:
        user = User(email=f"{uuid.uuid4().hex[:8]}@example.com", password_hash="h")
        s.add(user)
        s.flush()
        workspace = Workspace(owner_id=user.id, name="W")
        s.add(workspace)
        s.flush()
        agent = Agent(workspace_id=workspace.id, name="Dev X", category="development")
        s.add(agent)
        s.flush()
        version = AgentVersion(agent_id=agent.id, version=1, configuration=DEVELOPER_CONFIG)
        s.add(version)
        s.flush()
        agent.active_version_id, agent.status = version.id, AgentStatus.ACTIVE
        software = kind == WorkflowKind.SOFTWARE_PROJECT
        workflow = Workflow(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            prompt="Build a tool.",
            autonomy_policy=AutonomyPolicy.AUTONOMOUS,
            max_parallel_tasks=3,
            kind=kind,
            project_spec=(
                {"name": "tool", "stack_profile": "python-3.12", "architecture": "a"}
                if software
                else None
            ),
        )
        s.add(workflow)
        s.flush()
        rows = [
            WorkflowTask(
                workspace_id=workspace.id,
                workflow_id=workflow.id,
                title=f"t{i}",
                description="d",
                required_skills=["code_generation"],
                acceptance_criteria=[{"id": "c1", "description": "Done."}],
                deliverables=["Files"],
                file_scope=scope if software else [],
                effort_points=3 if software else None,
                assigned_agent_id=agent.id,
                assigned_agent_version_id=version.id,
            )
            for i, scope in enumerate(SCOPES)
        ]
        s.add_all(rows)
        s.flush()
        s.add_all(
            TaskDependency(
                task_id=rows[d].id, depends_on_task_id=rows[p].id, workflow_id=workflow.id
            )
            for d, p in ((1, 0), (2, 1))
        )
        s.flush()
        for target in (WorkflowStatus.PLANNING, WorkflowStatus.READY, WorkflowStatus.RUNNING):
            engine.transition_workflow(s, workflow, target, actor=Actor.USER)
        _drop_side_requests(s, workflow.id)
        s.commit()
        return Graph(workspace.id, workflow.id, [t.id for t in rows], 3, [(1, 0), (2, 1)])


def _events(admin: Factory, graph: Graph, event_type: str) -> list[OutboxEvent]:
    with session_scope(admin) as s:
        return list(
            s.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.workflow_id == graph.workflow_id,
                    OutboxEvent.event_type == event_type,
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
            )
        )


def _run_task(admin: Factory, worker: Factory, graph: Graph, index: int) -> runner.Claim:
    """Dispatch -> claim -> Developer runtime on the mock model -> verification -> COMPLETED."""
    task_id = graph.task_ids[index]
    (event,) = [e for e in _events(admin, graph, outbox.TASK_DISPATCH) if e.task_id == task_id]
    claim = _claim(worker, event)
    assert isinstance(claim, runner.Claim)
    submission = runner.run_agent(claim, models=_MODELS, is_current=lambda: True)
    with session_scope(worker) as s:
        assert runner.finish(s, claim, submission) == "submitted"
    _verify(admin, worker, graph, task_id)
    return claim


def _apply(worker: Factory, workspaces: ProjectWorkspaces, event: OutboxEvent) -> str:
    assert event.task_id is not None
    with session_scope(worker) as s:
        return apply_job.run(
            s,
            workspaces,
            event_id=event.id,
            workspace_id=event.workspace_id,
            workflow_id=uuid.UUID(event.payload["workflow_id"]),
            task_id=event.task_id,
        )


def test_accepted_tasks_are_applied_to_the_workspace_once_each(
    admin_factory: Factory, worker_factory: Factory, tmp_path: Path
) -> None:
    workspaces = ProjectWorkspaces(tmp_path / "workspaces")
    graph = _start_project(admin_factory)

    claims = [_run_task(admin_factory, worker_factory, graph, i) for i in range(len(SCOPES))]

    # The application task saw the setup task's accepted files in its contract.
    contract = runner.build_contract(claims[1], AgentConfiguration.model_validate(DEVELOPER_CONFIG))
    assert contract.file_scope == ["src/"] and contract.stack_profile == "python-3.12"
    assert [f.path for f in contract.dependency_files] == ["README.md", "pyproject.toml"]

    applies = _events(admin_factory, graph, outbox.PROJECT_APPLY)
    assert [e.task_id for e in applies] == graph.task_ids
    assert [_apply(worker_factory, workspaces, e) for e in applies] == ["applied"] * 3
    assert [_apply(worker_factory, workspaces, e) for e in applies] == ["duplicate"] * 3

    project = workspaces.project_path(graph.workspace_id, graph.workflow_id)
    assert (project / "pyproject.toml").read_text().startswith("[project]")
    assert (project / "src" / "mock_module.py").is_file()
    assert (project / "tests" / "test_mock.py").is_file()
    log = subprocess.run(  # noqa: S603 (test-built arguments)
        ["git", "-C", str(project), "log", "--format=%s"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert log == ["t2 (attempt 1)", "t1 (attempt 1)", "t0 (attempt 1)", "Start project"]

    with session_scope(admin_factory) as s:
        rows = list(
            s.scalars(select(WorkspaceApplication).order_by(WorkspaceApplication.created_at))
        )
        statuses = {t.id: t.status for t in s.scalars(select(WorkflowTask))}
    assert [r.status for r in rows] == [WorkspaceApplicationStatus.APPLIED] * 3
    assert all(r.commit_sha and r.head_sha and r.tree_sha256 for r in rows)
    assert set(statuses.values()) == {TaskStatus.COMPLETED}


def test_general_workflows_are_never_applied(
    admin_factory: Factory, worker_factory: Factory, tmp_path: Path
) -> None:
    graph = _start_project(admin_factory, WorkflowKind.GENERAL)
    _run_task(admin_factory, worker_factory, graph, 0)
    assert _events(admin_factory, graph, outbox.PROJECT_APPLY) == []
    assert not (tmp_path / "workspaces").exists()


def test_application_failures_are_recorded_with_a_code(
    admin_factory: Factory, worker_factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _start_project(admin_factory)
    _run_task(admin_factory, worker_factory, graph, 0)
    (event,) = _events(admin_factory, graph, outbox.PROJECT_APPLY)
    monkeypatch.setattr("app.projects.workspace.shutil.which", lambda name: None)

    assert _apply(worker_factory, ProjectWorkspaces(tmp_path / "w"), event) == "failed"
    with session_scope(admin_factory) as s:
        (row,) = s.scalars(select(WorkspaceApplication))
    assert (row.status, row.error_code, row.head_sha) == (
        WorkspaceApplicationStatus.FAILED,
        "GIT_UNAVAILABLE",
        None,
    )
