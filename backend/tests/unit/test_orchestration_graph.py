"""The orchestration graph (Step 4.1, ADR-0025): deterministic routing to one next step."""

import pytest
from langsmith import utils as langsmith_utils

from app.orchestration.graph import GRAPH_VERSION, NextStep, TaskView, assess, build_graph


def _assess(status: str, tasks: list[TaskView]) -> NextStep:
    step, checkpoint_id = assess(status, tasks)
    assert checkpoint_id is None  # no checkpointer
    return step


def _task(index: int, status: str, *, assigned: bool = False, required: bool = True) -> TaskView:
    return TaskView(id=f"t{index}", status=status, required=required, assigned=assigned)


def test_the_graph_is_a_compiled_langgraph_with_one_node_per_outcome() -> None:
    graph = build_graph()
    assert set(graph.get_graph().nodes) >= {
        "count_tasks",
        "finished",
        "workflow_step",
        "assign_agents",
        "running",
        "awaiting_verification",
        "blocked",
    }
    assert GRAPH_VERSION == "orchestration/v1"


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        ("PENDING", "start_planning"),
        ("PLANNING", "complete_plan"),
        ("AWAITING_APPROVAL", "approve_plan"),
        ("READY", "start_workflow"),
        ("PAUSED", "resume_workflow"),
    ],
)
def test_workflows_before_or_between_runs_wait_for_people(status: str, kind: str) -> None:
    step = _assess(status, [_task(1, "READY")])
    assert step["kind"] == kind and step["actor"] == "people" and step["task_ids"] == []


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "CANCELLED"])
def test_finished_workflows_need_nothing(status: str) -> None:
    step = _assess(status, [_task(1, "COMPLETED")])
    assert step == {
        "kind": "finished",
        "actor": "none",
        "summary": f"The workflow is {status.lower()}.",
        "task_ids": [],
    }


def test_unassigned_and_failed_tasks_need_people_first() -> None:
    step = _assess(
        "RUNNING",
        [
            _task(1, "READY"),
            _task(2, "REWORK_REQUIRED", assigned=True),
            _task(3, "READY", assigned=True),  # assigned, about to be dispatched: not counted
            _task(4, "IN_PROGRESS", assigned=True),
            _task(5, "SUBMITTED", assigned=True),
        ],
    )
    assert step["kind"] == "assign_agents" and step["actor"] == "people"
    assert step["summary"] == "Assign agents to 2 tasks (1 task running, 1 awaiting verification)."
    assert step["task_ids"] == ["t1", "t2"]


def test_work_in_progress_belongs_to_agents() -> None:
    step = _assess(
        "RUNNING",
        [_task(1, "QUEUED", assigned=True), _task(2, "SUBMITTED", assigned=True)],
    )
    assert step["kind"] == "running" and step["actor"] == "agents"
    assert step["summary"] == "Agents are working on 1 task; 1 awaiting verification."
    assert step["task_ids"] == ["t1"]


def test_submitted_work_waits_for_verification() -> None:
    step = _assess("RUNNING", [_task(1, "SUBMITTED", assigned=True), _task(2, "COMPLETED")])
    assert step["kind"] == "awaiting_verification" and step["actor"] == "platform"
    assert "Phase 5" in step["summary"] and step["task_ids"] == ["t1"]


@pytest.mark.parametrize(
    ("tasks", "summary"),
    [
        ([_task(1, "BLOCKED")], "1 task blocked; check their dependencies."),
        ([_task(1, "PENDING"), _task(2, "PENDING")], "2 tasks waiting for their dependencies."),
        ([], "Nothing can progress; add or reassign tasks."),
    ],
)
def test_nothing_runnable_is_blocked(tasks: list[TaskView], summary: str) -> None:
    step = _assess("BLOCKED", tasks)
    assert step["kind"] == "blocked" and step["summary"] == summary


def test_task_ids_are_capped() -> None:
    step = _assess("RUNNING", [_task(i, "READY") for i in range(80)])
    assert len(step["task_ids"]) == 50
    assert step["summary"] == "Assign agents to 80 tasks."


def test_langsmith_tracing_stays_off_whatever_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    assert langsmith_utils.tracing_is_enabled() is False
