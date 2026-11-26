"""Project progress (Step 9.7, ADR-0053): weighted verified progress, milestones, phases and a
time-remaining range, over a real project lifecycle."""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import Session, sessionmaker

from app.artifacts.storage import LocalArtifactStorage
from app.db.sync_session import session_scope
from app.execution import outbox
from app.models import Workflow, WorkspaceRole
from app.projects.workspace import ProjectWorkspaces
from app.services.authorization import WorkspaceAccess
from app.services.progress import ProgressResponse, project_progress
from tests.integration.test_execution_scenarios import Graph
from tests.integration.test_project_integration import _FakeSandbox, _integrate
from tests.integration.test_project_packaging import _package
from tests.integration.test_project_workspace import (
    SCOPES,
    _apply,
    _events,
    _run_task,
    _start_project,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

Factory = sessionmaker[Session]


async def _progress(engine: AsyncEngine, admin: Factory, graph: Graph) -> ProgressResponse:
    with session_scope(admin) as s:
        workflow = s.get(Workflow, graph.workflow_id)
        assert workflow is not None
        owner = workflow.created_by_user_id
    access = WorkspaceAccess(graph.workspace_id, owner, WorkspaceRole.OWNER)
    async with AsyncSession(engine, expire_on_commit=False, autoflush=False) as session:
        return await project_progress(session, access, graph.workflow_id)


async def test_progress_counts_only_verified_work_and_milestones(
    engine: AsyncEngine, admin_factory: Factory, worker_factory: Factory, tmp_path: Path
) -> None:
    workspaces = ProjectWorkspaces(tmp_path / "w")
    storage = LocalArtifactStorage(tmp_path / "a", max_bytes=10 * 1024 * 1024)
    graph = _start_project(admin_factory)  # 3 tasks x 3 effort points; +1 and +1 milestones

    start = await _progress(engine, admin_factory, graph)
    assert (start.phase, start.percent, start.total_weight) == ("BUILDING", 0, 9 + 1 + 1)
    assert start.remaining.confidence == "none" and start.remaining.low_seconds is None
    assert (start.integration, start.package) == ("pending", "pending")

    _run_task(admin_factory, worker_factory, graph, 0)
    one = await _progress(engine, admin_factory, graph)
    assert one.completed_weight == 3 and one.percent == 3 * 100 // 11
    assert one.tasks.completed == 1 and one.remaining.samples == 1
    assert one.remaining.confidence == "low" and one.remaining.low_seconds is not None

    for index in range(1, len(SCOPES)):
        _run_task(admin_factory, worker_factory, graph, index)
    for event in _events(admin_factory, graph, outbox.PROJECT_APPLY):
        _apply(worker_factory, workspaces, event)
    integrating = await _progress(engine, admin_factory, graph)
    assert integrating.phase == "INTEGRATING" and integrating.percent == 9 * 100 // 11
    assert (integrating.remaining.low_seconds, integrating.remaining.high_seconds) == (0, 0)

    integrate = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)[-1]
    _integrate(worker_factory, workspaces, _FakeSandbox(), integrate)
    packaging = await _progress(engine, admin_factory, graph)
    assert (packaging.phase, packaging.integration) == ("PACKAGING", "passed")
    assert packaging.percent == 10 * 100 // 11  # never 100 before the deliverable exists

    (event,) = _events(admin_factory, graph, outbox.PROJECT_PACKAGE)
    assert _package(worker_factory, workspaces, storage, event) == "ready"
    done = await _progress(engine, admin_factory, graph)
    assert (done.phase, done.percent, done.package) == ("COMPLETED", 100, "ready")


async def test_a_failed_gate_needs_attention(
    engine: AsyncEngine, admin_factory: Factory, worker_factory: Factory, tmp_path: Path
) -> None:
    workspaces = ProjectWorkspaces(tmp_path / "w")
    graph = _start_project(admin_factory)
    for index in range(len(SCOPES)):
        _run_task(admin_factory, worker_factory, graph, index)
    for event in _events(admin_factory, graph, outbox.PROJECT_APPLY):
        _apply(worker_factory, workspaces, event)
    integrate = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)[-1]
    _integrate(worker_factory, workspaces, _FakeSandbox(succeed=False), integrate)

    blocked = await _progress(engine, admin_factory, graph)
    assert (blocked.phase, blocked.integration) == ("NEEDS_ATTENTION", "failed")
    assert blocked.percent == 9 * 100 // 11


async def test_ready_work_without_an_agent_needs_attention(
    engine: AsyncEngine, admin_factory: Factory
) -> None:
    # Live Gemini run: a task whose skills no single agent had sat READY with no agent while the
    # page said "Building". It needs a person, so the project says so.
    from app.models import TaskStatus, WorkflowTask

    graph = _start_project(admin_factory)
    with session_scope(admin_factory) as s:
        task = s.get(WorkflowTask, graph.task_ids[0])
        assert task is not None and task.status in (TaskStatus.ASSIGNED, TaskStatus.QUEUED)
        task.status, task.assigned_agent_id, task.assigned_agent_version_id = (
            TaskStatus.READY,
            None,
            None,
        )
        s.commit()
    progress = await _progress(engine, admin_factory, graph)
    assert progress.phase == "NEEDS_ATTENTION" and progress.tasks.needs_attention == 1
