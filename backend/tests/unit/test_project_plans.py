"""Software project plans (ADR-0048): plan schema version 2, file ownership scopes, stack
profiles, the planner's kind checks and its deterministic mock plans."""

import copy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.llm.types import ChatMessage, ModelRequest
from app.models import WorkflowKind
from app.planning import plan
from app.planning.schema import PlanProposal
from app.projects.profiles import PROFILES
from app.projects.scope import covers, in_scope, normalize_entry, overlaps


def _task(key: str, scope: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "key": key,
        "title": f"Task {key}",
        "required_skills": ["code_generation"],
        "deliverables": ["files"],
        "acceptance_criteria": [{"id": "c1", "description": "Done."}],
        "file_scope": scope,
        "effort_points": 3,
        **extra,
    }


VALID: dict[str, Any] = {
    "schema_version": 2,
    "summary": "A tool",
    "project": {
        "name": "tool",
        "stack_profile": "python-3.12",
        "architecture": "One module.",
    },
    "tasks": [
        _task("t1", ["pyproject.toml", "README.md"]),
        _task("t2", ["src/tool/"], depends_on=["t1"]),
        _task("t3", ["tests/"], depends_on=["t2"]),
    ],
}


def _plan(**changes: Any) -> dict[str, Any]:
    data = copy.deepcopy(VALID)
    data.update(changes)
    return data


def test_a_valid_project_plan() -> None:
    proposal = PlanProposal.model_validate(VALID)
    assert proposal.project is not None
    assert proposal.tasks[1].file_scope == ["src/tool/"]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (_plan(schema_version=1), "schema_version 2"),
        (_plan(project=None), "version 2 plan needs a project"),
        (
            _plan(project={**VALID["project"], "stack_profile": "cobol"}),
            "stack_profile must be one of",
        ),
        (
            _plan(tasks=[_task("t1", ["pyproject.toml", "README.md"]), _task("t2", [])]),
            "t2 needs a file_scope",
        ),
        (
            _plan(
                tasks=[
                    _task("t1", ["pyproject.toml", "README.md"]),
                    _task("t2", ["src/"], effort_points=None),
                ]
            ),
            "t2 needs effort_points",
        ),
        (
            _plan(
                tasks=[
                    _task("t1", ["pyproject.toml", "README.md", "src/"]),
                    _task("t2", ["src/a.py"]),
                ]
            ),
            "own overlapping files",
        ),
        (
            _plan(
                tasks=[
                    _task("t1", ["pyproject.toml", "README.md", "SRC/"]),
                    _task("t2", ["src/a.py"]),
                ]
            ),
            "own overlapping files",
        ),
        (_plan(tasks=[_task("t1", ["pyproject.toml", "README.md", "../x"])]), "plain names"),
        (_plan(tasks=[_task("t1", ["pyproject.toml", "README.md", ".env"])]), "environment"),
        (_plan(tasks=[_task("t1", ["pyproject.toml", "README.md", ".git/"])]), "version-control"),
        (_plan(tasks=[_task("t1", ["pyproject.toml", "README.md", "a/", "a/b.py"])]), "overlap"),
        (_plan(tasks=[_task("t1", ["pyproject.toml", "README.md"], effort_points=14)]), "13"),
    ],
)
def test_invalid_project_plans(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        PlanProposal.model_validate(data)


def test_general_plans_carry_no_project_fields() -> None:
    general: dict[str, Any] = {
        "summary": "s",
        "tasks": [
            {
                "key": "t1",
                "title": "Write",
                "required_skills": ["report_writing"],
                "deliverables": ["report"],
                "acceptance_criteria": [{"id": "c1", "description": "Done."}],
            }
        ],
    }
    assert PlanProposal.model_validate(general).project is None
    scoped = copy.deepcopy(general)
    scoped["tasks"][0]["file_scope"] = ["src/"]
    with pytest.raises(ValidationError, match="project plans only"):
        PlanProposal.model_validate(scoped)


def test_scope_matching_ignores_case_and_respects_folders() -> None:
    assert covers("src/", "src/app/main.py")
    assert covers("SRC/", "src/a.py")
    assert covers("README.md", "readme.md")
    assert not covers("src", "src/a.py")  # an exact file, not a folder
    assert not covers("src/", "srcx/a.py")
    assert overlaps("src/", "src/a/")
    assert overlaps("src/a/", "src/")
    assert not overlaps("src/a/", "src/b/")
    assert in_scope(["tests/", "README.md"], "tests/test_x.py")
    assert not in_scope(["tests/"], "src/x.py")
    assert normalize_entry("src/app/") == "src/app/"
    with pytest.raises(ValueError):
        normalize_entry("/abs/")


def test_profiles_share_their_manifests_and_readme() -> None:
    for profile in PROFILES.values():
        assert "README.md" in profile.shared_files
        assert profile.dependencies
        assert all(path.endswith("/") for path in profile.test_paths)


def test_project_issue_matches_plan_kind_to_workflow_kind() -> None:
    project = PlanProposal.model_validate(VALID)
    general = PlanProposal.model_validate(
        {
            "summary": "s",
            "tasks": [
                {
                    "key": "t1",
                    "title": "Write",
                    "required_skills": ["report_writing"],
                    "deliverables": ["report"],
                    "acceptance_criteria": [{"id": "c1", "description": "Done."}],
                }
            ],
        }
    )
    software = WorkflowKind.SOFTWARE_PROJECT
    assert plan.project_issue(software, None, project) is None
    assert plan.project_issue(software, None, general) == "PLAN_PROJECT_MISSING"
    assert plan.project_issue(WorkflowKind.GENERAL, None, project) == "PLAN_PROJECT_UNEXPECTED"
    assert plan.project_issue(WorkflowKind.GENERAL, None, general) is None
    assert plan.project_issue(software, "node-24-typescript", project) == "PLAN_PROFILE_CHANGED"


def _request(user: str) -> ModelRequest:
    return ModelRequest(
        purpose=plan.PURPOSE,
        messages=[ChatMessage(role="system", content="s"), ChatMessage(role="user", content=user)],
    )


@pytest.mark.parametrize("profile_id", sorted(PROFILES))
def test_mock_software_plans_are_valid_for_every_profile(profile_id: str) -> None:
    section = plan._profiles_section(profile_id)
    proposal = PlanProposal.model_validate_json(plan.mock_plan(_request(section)))
    assert proposal.project is not None
    assert proposal.project.stack_profile == profile_id

    completed = plan._delimited("completed_tasks", json.dumps([{"key": "done-1"}]))
    unfinished = plan._delimited("unfinished_tasks", json.dumps([{"title": "API"}]))
    replan = PlanProposal.model_validate_json(
        plan.mock_plan(_request("\n".join([section, completed, unfinished])))
    )
    assert replan.project is not None
    assert replan.tasks[0].depends_on_completed == ["done-1"]


def test_shared_files_without_an_owner_are_reported() -> None:
    partial = PlanProposal.model_validate(_plan(tasks=[_task("t1", ["src/"])]))
    assert partial.unowned_shared_files() == ["pyproject.toml", "README.md"]
    assert PlanProposal.model_validate(VALID).unowned_shared_files() == []
    software = WorkflowKind.SOFTWARE_PROJECT
    assert plan.project_issue(software, None, partial) == "PLAN_SHARED_FILE_UNOWNED"
    assert plan.project_issue(software, None, partial, replan=True) is None


def test_profiles_name_their_sandbox_images_and_build_contexts() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "infrastructure" / "sandbox"
    for profile in PROFILES.values():
        assert profile.image == f"agentflow-sandbox/{profile.id}:1"
        assert (root / profile.id / "Dockerfile").is_file()
