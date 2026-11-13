"""DAG validation of plan drafts (Step 2.4)."""

import json
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.llm.gateway import ModelGateway
from app.llm.mock import MockModelAdapter
from app.llm.types import CallContext, ChatMessage, ModelRequest
from app.planning.dag import analyse_plan, find_cycle, stages
from app.planning.plan import PURPOSE, PlanJob, generate
from app.planning.schema import PlannedTask, PlanProposal
from app.services.usage import CallRecord
from tests.conftest import make_settings


def _task(
    key: str, *deps: str, required: bool = True, skills: tuple[str, ...] = ("research",)
) -> dict[str, Any]:
    return {
        "key": key,
        "title": f"Task {key}",
        "required_skills": list(skills),
        "deliverables": ["Notes"],
        "acceptance_criteria": [{"id": "c1", "description": "Done"}],
        "required": required,
        "depends_on": list(deps),
    }


def _tasks(*tasks: dict[str, Any]) -> list[PlannedTask]:
    return [PlannedTask.model_validate(t) for t in tasks]


def _codes(report_issues: Any) -> list[str]:
    return [issue.code for issue in report_issues]


def test_stages_follow_the_longest_dependency_chain() -> None:
    tasks = _tasks(_task("a"), _task("b"), _task("c", "a"), _task("d", "c", "b"), _task("e", "a"))
    assert stages(tasks) == [["a", "b"], ["c", "e"], ["d"]]


def test_cycles_are_found_including_the_tasks_behind_them() -> None:
    tasks = _tasks(_task("a", "c"), _task("b", "a"), _task("c", "b"), _task("d", "c"), _task("e"))
    assert find_cycle(tasks) == ["a", "b", "c", "d"]
    assert find_cycle(_tasks(_task("a"), _task("b", "a"))) == []


def test_a_cyclic_plan_is_an_error_without_stages() -> None:
    report = analyse_plan(_tasks(_task("a", "b"), _task("b", "a")), max_parallel_tasks=3)
    assert report.has_errors
    assert _codes(report.issues) == ["DEPENDENCY_CYCLE"]
    assert report.issues[0].task_keys == ("a", "b")
    assert report.stages == []


def test_warnings_explain_risks_without_blocking() -> None:
    tasks = _tasks(
        _task("a", required=False),
        _task("b"),
        _task("c", "a", skills=("research", "code_generation")),
        _task("d"),
    )
    report = analyse_plan(tasks, max_parallel_tasks=2, available_skills={"research"})

    assert not report.has_errors
    by_code = {issue.code: issue for issue in report.issues}
    assert by_code["REQUIRED_DEPENDS_ON_OPTIONAL"].task_keys == ("c", "a")
    assert by_code["SKILL_NOT_AVAILABLE"].task_keys == ("c",)
    assert by_code["SKILL_NOT_AVAILABLE"].details == {"skill": "code_generation"}
    assert by_code["STAGE_EXCEEDS_PARALLEL_LIMIT"].task_keys == ("a", "b", "d")
    assert all(issue.severity == "warning" for issue in report.issues)


def test_agent_coverage_is_skipped_when_unknown() -> None:
    report = analyse_plan(_tasks(_task("a", skills=("code_generation",))), max_parallel_tasks=3)
    assert report.issues == []


def test_the_plan_schema_rejects_cycles() -> None:
    with pytest.raises(ValidationError, match="dependency cycle among tasks: t1, t2"):
        PlanProposal.model_validate(
            {"summary": "s", "tasks": [_task("t1", "t2"), _task("t2", "t1")]}
        )


class Recorder:
    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    def __call__(self, record: CallRecord) -> uuid.UUID:
        self.records.append(record)
        return uuid.uuid4()


def test_a_cyclic_reply_is_invalid_output_and_retried() -> None:
    adapter, recorder = MockModelAdapter(defaults={}), Recorder()
    cyclic = {"summary": "s", "tasks": [_task("t1", "t2"), _task("t2", "t1")]}
    adapter.script(
        PURPOSE, json.dumps(cyclic), json.dumps({"summary": "s", "tasks": [_task("t1")]})
    )
    workspace, workflow = uuid.uuid4(), uuid.uuid4()
    job = PlanJob(
        request_id=uuid.uuid4(),
        workspace_id=workspace,
        workflow_id=workflow,
        context=CallContext(workspace_id=workspace, workflow_id=workflow),
        request=ModelRequest(purpose=PURPOSE, messages=[ChatMessage(role="user", content="plan")]),
    )

    outcome = generate(ModelGateway(make_settings(), adapter, recorder), job, sleep=lambda _: None)

    assert isinstance(outcome, PlanProposal)
    assert [r.status.value for r in recorder.records] == ["INVALID_OUTPUT", "SUCCEEDED"]
