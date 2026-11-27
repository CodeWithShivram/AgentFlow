"""Integration rework (Step 9.9, ADR-0056): a failing gate asks the owners of the failing files
for fixes, bounded in rounds; fix tasks run like any task and the gate runs again."""

import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.sync_session import session_scope
from app.execution import outbox, runner
from app.models import (
    IntegrationStatus,
    ProjectIntegration,
    TaskStatus,
    Workflow,
    WorkflowStatus,
    WorkflowTask,
)
from app.projects import integration_rework
from app.projects.context import fix_context
from app.projects.workspace import ProjectWorkspaces
from app.sandbox.runner import SandboxResult
from tests.integration.test_execution_scenarios import Graph, _claim, _verify
from tests.integration.test_project_integration import (
    _built_and_applied,
    _FakeSandbox,
    _integrate,
    _workflow,
)
from tests.integration.test_project_workspace import _MODELS, _apply, _events

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
FAILURE = "FAILED tests/test_mock.py::test_mock - assert 19 == 18"


class _FailingSandbox(_FakeSandbox):
    def __init__(self, output: str) -> None:
        super().__init__(succeed=False)
        self.output = output

    def run(
        self, image: str, project: Path, command: Sequence[str], collect: Sequence[str] = ()
    ) -> SandboxResult:
        return SandboxResult(exit_code=1, timed_out=False, output=self.output, duration_ms=5)


def _tasks(admin: Factory, graph: Graph) -> list[WorkflowTask]:
    with session_scope(admin) as s:
        return list(
            s.scalars(
                select(WorkflowTask)
                .where(WorkflowTask.workflow_id == graph.workflow_id)
                .order_by(WorkflowTask.created_at)
            )
        )


def _run_task_by_id(admin: Factory, worker: Factory, graph: Graph, task_id: uuid.UUID) -> None:
    # The scheduler may have written more than one dispatch for the task; the current one is
    # the one a worker can claim (the others are stale and change nothing).
    events = [e for e in _events(admin, graph, outbox.TASK_DISPATCH) if e.task_id == task_id]
    claims = (_claim(worker, event) for event in reversed(events))
    claim = next((c for c in claims if isinstance(c, runner.Claim)), None)
    assert claim is not None
    submission = runner.run_agent(claim, models=_MODELS, is_current=lambda: True)
    with session_scope(worker) as s:
        assert runner.finish(s, claim, submission) == "submitted"
    _verify(admin, worker, graph, task_id)


def _apply_new(
    worker: Factory,
    workspaces: ProjectWorkspaces,
    admin: Factory,
    graph: Graph,
    done: set[uuid.UUID],
) -> None:
    for event in _events(admin, graph, outbox.PROJECT_APPLY):
        if event.id not in done:
            assert _apply(worker, workspaces, event) == "applied"
            done.add(event.id)


def test_a_failing_gate_asks_the_owner_for_a_fix_and_then_completes(
    admin_factory: Factory,
    worker_factory: Factory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspaces = ProjectWorkspaces(tmp_path / "w")
    monkeypatch.setattr(runner, "project_workspaces", lambda: workspaces)
    graph = _built_and_applied(admin_factory, worker_factory, workspaces)
    applied = {e.id for e in _events(admin_factory, graph, outbox.PROJECT_APPLY)}

    first = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)[-1]
    assert (
        _integrate(worker_factory, workspaces, _FailingSandbox(FAILURE), first) == "fixes_requested"
    )
    assert _workflow(admin_factory, graph.workflow_id).status == WorkflowStatus.RUNNING

    tasks = _tasks(admin_factory, graph)
    fixes = [t for t in tasks if t.fixes_task_id is not None]
    tests_task = next(t for t in tasks if t.file_scope == ["tests/"] and t.fixes_task_id is None)
    assert [f.fixes_task_id for f in fixes] == [tests_task.id]  # only the owner of the named file
    fix = fixes[0]
    assert fix.file_scope == ["tests/"] and fix.required
    assert fix.assigned_agent_version_id == tests_task.assigned_agent_version_id
    assert fix.acceptance_criteria[-1]["id"] == "integration"

    with session_scope(admin_factory) as s:
        workflow = s.get(Workflow, graph.workflow_id)
        task = s.get(WorkflowTask, fix.id)
        assert workflow is not None and task is not None
        feedback, current, others = fix_context(s, workflow, task, workspaces)
    assert feedback == [
        {"check": "compile", "output": FAILURE},
        {"check": "tests", "output": FAILURE},
    ]
    assert [f["path"] for f in current] == ["tests/test_mock.py"]
    # The rest of the project, read-only: a tests fix must see the code it tests.
    assert {f["path"] for f in others} == {"pyproject.toml", "README.md", "src/mock_module.py"}

    _run_task_by_id(admin_factory, worker_factory, graph, fix.id)
    _apply_new(worker_factory, workspaces, admin_factory, graph, applied)
    second = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)[-1]
    assert second.id != first.id
    assert _integrate(worker_factory, workspaces, _FakeSandbox(), second) == "passed"
    assert _workflow(admin_factory, graph.workflow_id).status == WorkflowStatus.COMPLETED


def test_fix_rounds_are_bounded_then_people_decide(
    admin_factory: Factory,
    worker_factory: Factory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspaces = ProjectWorkspaces(tmp_path / "w")
    # Fix tasks read the current files from this test's workspace, as workers do from theirs.
    monkeypatch.setattr(runner, "project_workspaces", lambda: workspaces)
    graph = _built_and_applied(admin_factory, worker_factory, workspaces)
    applied = {e.id for e in _events(admin_factory, graph, outbox.PROJECT_APPLY)}
    unattributed = "E   something failed somewhere"

    for _round in range(integration_rework.MAX_FIX_ROUNDS):
        event = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)[-1]
        sandbox = _FailingSandbox(unattributed)
        assert _integrate(worker_factory, workspaces, sandbox, event) == "fixes_requested"
        pending = [t for t in _tasks(admin_factory, graph) if t.status != TaskStatus.COMPLETED]
        # No file named: every current owner (one per scope) is asked.
        assert sorted(tuple(t.file_scope) for t in pending) == [
            ("pyproject.toml", "README.md"),
            ("src/",),
            ("tests/",),
        ]
        for task in pending:
            _run_task_by_id(admin_factory, worker_factory, graph, task.id)
        _apply_new(worker_factory, workspaces, admin_factory, graph, applied)

    last = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)[-1]
    assert _integrate(worker_factory, workspaces, _FailingSandbox(unattributed), last) == "failed"
    assert _workflow(admin_factory, graph.workflow_id).status == WorkflowStatus.BLOCKED
    with session_scope(admin_factory) as s:
        runs = list(
            s.scalars(
                select(ProjectIntegration).where(
                    ProjectIntegration.workflow_id == graph.workflow_id
                )
            )
        )
    assert [r.status for r in runs].count(
        IntegrationStatus.FAILED
    ) == integration_rework.MAX_FIX_ROUNDS + 1


def test_responsible_tasks_prefer_the_newest_owner() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)

    def task(scope: list[str], minutes: int) -> WorkflowTask:
        return WorkflowTask(
            id=uuid.uuid4(), file_scope=scope, status=TaskStatus.COMPLETED, removed_at=None,
            created_at=now + timedelta(minutes=minutes),
        )  # fmt: skip

    original, newer = task(["src/"], 0), task(["src/"], 5)
    tests = task(["tests/"], 1)
    chosen = integration_rework.responsible_tasks(
        [original, newer, tests], "Traceback: src/app.py line 3", ["src/app.py", "tests/t.py"]
    )
    assert chosen == [newer]


def test_the_recovery_sweep_closes_a_lost_integration_run_and_asks_again(
    admin_factory: Factory, worker_factory: Factory, tmp_path: Path
) -> None:
    # Live run: the gate crashed after claiming its run, leaving the project "Integrating".
    from datetime import UTC, datetime, timedelta

    from app.execution import recovery
    from app.projects import integration_job

    workspaces = ProjectWorkspaces(tmp_path / "w")
    graph = _built_and_applied(admin_factory, worker_factory, workspaces)
    event = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)[-1]
    with session_scope(worker_factory) as s:
        job = integration_job.claim(
            s, workspaces, event_id=event.id, workspace_id=event.workspace_id,
            workflow_id=graph.workflow_id,
        )  # fmt: skip
    assert isinstance(job, integration_job.IntegrationJob)  # claimed, then the worker is lost
    before = len(_events(admin_factory, graph, outbox.PROJECT_INTEGRATE))

    soon = recovery.recover_once(worker_factory, now=datetime.now(UTC) + timedelta(minutes=5))
    assert soon.lost_integrations == 0  # still within the time a worker may need
    later = recovery.recover_once(
        worker_factory, now=datetime.now(UTC) + integration_job.STALE_AFTER + timedelta(minutes=1)
    )
    assert later.lost_integrations == 1
    with session_scope(admin_factory) as s:
        run = s.get(ProjectIntegration, job.integration_id)
        assert run is not None
        assert (run.status, run.error_code) == (IntegrationStatus.NOT_RUN, "WORKER_LOST")
    retry = _events(admin_factory, graph, outbox.PROJECT_INTEGRATE)
    assert len(retry) == before + 1
    assert _integrate(worker_factory, workspaces, _FakeSandbox(), retry[-1]) == "passed"
    assert _workflow(admin_factory, graph.workflow_id).status == WorkflowStatus.COMPLETED
