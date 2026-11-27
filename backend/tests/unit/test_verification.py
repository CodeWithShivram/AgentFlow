"""Deterministic verification checks (Step 5.1, ADR-0030): pure, versioned, and free of agent
text in what they record."""

import json
from typing import Any

import pytest

from app.agents.specialists import DeveloperAgent, QAAgent, WriterAgent
from app.verification import deterministic
from app.verification.deterministic import CheckOutcome, Severity, Subject

CRITERIA = [{"id": "c1", "description": "Covers the topic."}, {"id": "c2", "description": "Short."}]


def _configuration(
    ref: str = "workspace://prompts/w/v1", tools: list[Any] | None = None
) -> dict[str, Any]:
    return {
        "system_instruction_ref": ref,
        "capabilities": ["report_writing"],
        "tools": tools or [],
    }


def _submission(**changes: Any) -> dict[str, Any]:
    return {
        "summary": "Done.",
        "outputs": {"Report": "The report."},
        "criteria": [
            {"criterion_id": "c1", "status": "MET", "evidence": "Section 1."},
            {"criterion_id": "c2", "status": "MET", "evidence": "One page."},
        ],
        **changes,
    }


def _verify(
    submission: dict[str, Any] | None = None,
    *,
    criteria: list[dict[str, Any]] | None = None,
    deliverables: list[str] | None = None,
    schema: dict[str, Any] | None = None,
    configuration: dict[str, Any] | None = None,
) -> deterministic.Report:
    return deterministic.verify(
        Subject(
            submission=submission,
            acceptance_criteria=CRITERIA if criteria is None else criteria,
            deliverables=["Report"] if deliverables is None else deliverables,
            output_schema=schema,
            configuration=configuration or _configuration(),
        )
    )


def _codes(report: deterministic.Report) -> list[str]:
    return [issue.code for issue in report.issues]


def test_the_verifier_is_versioned() -> None:
    assert deterministic.VERIFIER == "deterministic/v4"


def test_a_complete_submission_passes_and_records_every_check() -> None:
    report = _verify(_submission(), schema={"type": "object", "required": ["Report"]})

    assert report.passed and report.issues == []
    assert report.checks_json() == [
        {"name": "submission", "outcome": "passed"},
        {"name": "criteria_coverage", "outcome": "passed"},
        {"name": "criteria_claims", "outcome": "passed"},
        {"name": "deliverables", "outcome": "passed"},
        {"name": "output_schema", "outcome": "passed"},
        {"name": "runtime_outputs", "outcome": "skipped"},
        {"name": "platform_note", "outcome": "skipped"},
        {"name": "file_scope", "outcome": "skipped"},  # not a software project task
        {"name": "fix_changes", "outcome": "skipped"},  # not an integration fix task
    ]


def test_checks_that_do_not_apply_are_skipped() -> None:
    report = _verify(_submission(criteria=[]), criteria=[], deliverables=[])
    outcomes = dict(report.checks)
    assert report.passed
    assert outcomes["criteria_claims"] == outcomes["deliverables"] == CheckOutcome.SKIPPED
    assert outcomes["output_schema"] == CheckOutcome.SKIPPED


@pytest.mark.parametrize(
    ("submission", "code"),
    [(None, "SUBMISSION_MISSING"), ({"summary": ""}, "SUBMISSION_INVALID")],
)
def test_an_unreadable_submission_fails_and_skips_the_rest(
    submission: dict[str, Any] | None, code: str
) -> None:
    report = _verify(submission)
    assert not report.passed and _codes(report) == [code]
    assert [outcome for _, outcome in report.checks[1:]] == [CheckOutcome.SKIPPED] * 8


def test_every_criterion_needs_exactly_one_claim_and_unknown_ids_are_not_stored() -> None:
    claims = [
        {"criterion_id": "c1", "status": "MET", "evidence": "a"},
        {"criterion_id": "c1", "status": "MET", "evidence": "b"},
        {"criterion_id": "agent-made-up", "status": "MET", "evidence": "c"},
    ]
    report = _verify(_submission(criteria=claims))

    assert not report.passed
    assert report.issues_json()[:3] == [
        {"code": "DUPLICATE_CRITERION_CLAIM", "severity": "error", "criterion_id": "c1"},
        {"code": "MISSING_CRITERION_CLAIM", "severity": "error", "criterion_id": "c2"},
        {"code": "UNKNOWN_CRITERION", "severity": "error"},
    ]
    assert "agent-made-up" not in json.dumps(report.issues_json())


def test_not_met_fails_and_partial_is_only_a_warning() -> None:
    partial = _submission(
        criteria=[
            {"criterion_id": "c1", "status": "PARTIAL", "evidence": "Half."},
            {"criterion_id": "c2", "status": "MET", "evidence": "Yes."},
        ]
    )
    report = _verify(partial)
    assert report.passed
    assert report.issues_json() == [
        {"code": "CRITERION_PARTIAL", "severity": "warning", "criterion_id": "c1"}
    ]

    not_met = _submission(
        criteria=[
            {"criterion_id": "c1", "status": "NOT_MET", "evidence": "No."},
            {"criterion_id": "c2", "status": "MET", "evidence": "Yes."},
        ]
    )
    report = _verify(not_met)
    assert not report.passed and report.error_codes() == ["CRITERION_NOT_MET"]
    assert report.issues[0].criterion_id == "c1"


@pytest.mark.parametrize("value", [None, "", "   ", [], {}])
def test_every_deliverable_needs_content(value: Any) -> None:
    outputs = {"Report": value} if value is not None else {}
    report = _verify(_submission(outputs=outputs))
    assert report.issues_json() == [
        {"code": "DELIVERABLE_MISSING", "severity": "error", "deliverable": "Report"}
    ]


def test_zero_and_false_count_as_content() -> None:
    report = _verify(
        _submission(outputs={"Count": 0, "Flag": False}), deliverables=["Count", "Flag"]
    )
    assert report.passed


def test_outputs_must_match_the_task_schema_without_quoting_them() -> None:
    schema = {
        "type": "object",
        "properties": {"Report": {"type": "string"}, "Score": {"type": "integer"}},
        "required": ["Report", "Score"],
    }
    agent_text = "agent text that must never be stored"
    report = _verify(_submission(outputs={"Report": "ok", "Score": agent_text}), schema=schema)

    assert not report.passed
    assert report.issues_json() == [
        {
            "code": "OUTPUT_SCHEMA_MISMATCH",
            "severity": "error",
            "location": "properties/Score/type",
        }
    ]
    assert agent_text not in json.dumps(report.issues_json())


def test_schema_mismatches_are_capped() -> None:
    schema = {"type": "object", "additionalProperties": {"type": "integer"}}
    outputs = {"Report": "x", **{f"k{i}": "x" for i in range(30)}}
    report = _verify(_submission(outputs=outputs), schema=schema)
    codes = _codes(report)
    assert codes.count("OUTPUT_SCHEMA_MISMATCH") == deterministic.MAX_SCHEMA_ISSUES
    assert codes[-1] == "OUTPUT_SCHEMA_MORE_MISMATCHES"


@pytest.mark.parametrize(
    ("schema", "code"),
    [
        ({"type": "not-a-type"}, "OUTPUT_SCHEMA_INVALID"),
        # Remote references are never fetched.
        ({"$ref": "https://schemas.example.invalid/report.json"}, "OUTPUT_SCHEMA_UNUSABLE"),
    ],
)
def test_a_schema_that_cannot_be_applied_fails_verification(
    schema: dict[str, Any], code: str
) -> None:
    report = _verify(_submission(), schema=schema)
    assert not report.passed and _codes(report) == [code]


def _developer_outputs() -> dict[str, Any]:
    proposal = {
        "path": "src/app.py",
        "language": "python",
        "purpose": "Change.",
        "content": "print('hi')\n",
    }
    return {"Report": "The report.", "proposed_files": [proposal], "proposed_tests": []}


def test_developer_outputs_and_the_platform_note_are_checked() -> None:
    configuration = _configuration(DeveloperAgent.instruction_ref)
    note = DeveloperAgent.platform_note
    good = _submission(outputs=_developer_outputs(), limitations=[note])
    assert _verify(good, configuration=configuration).passed

    without_note = _submission(outputs=_developer_outputs())
    assert _codes(_verify(without_note, configuration=configuration)) == ["PLATFORM_NOTE_MISSING"]

    bad_path = _developer_outputs()
    bad_path["proposed_files"][0]["path"] = "../outside.py"
    report = _verify(_submission(outputs=bad_path, limitations=[note]), configuration=configuration)
    assert _codes(report) == ["OUTPUTS_INVALID"]


def test_qa_test_cases_may_cover_only_known_criteria() -> None:
    configuration = _configuration(QAAgent.instruction_ref)
    case = {
        "id": "t1",
        "title": "Check",
        "covers": ["c9"],
        "kind": "manual",
        "steps": ["Step."],
        "expected": "Result.",
    }
    submission = _submission(
        outputs={"Report": "The report.", "test_cases": [case]},
        limitations=[QAAgent.platform_note],
    )
    assert _codes(_verify(submission, configuration=configuration)) == [
        "TEST_CASE_UNKNOWN_CRITERION"
    ]


def test_a_writer_has_no_runtime_specific_checks() -> None:
    report = _verify(_submission(), configuration=_configuration(WriterAgent.instruction_ref))
    outcomes = dict(report.checks)
    assert report.passed
    assert outcomes["runtime_outputs"] == outcomes["platform_note"] == CheckOutcome.SKIPPED


def test_issues_have_a_fixed_shape() -> None:
    issue = deterministic.Issue("X", Severity.WARNING, deliverable="Report")
    assert issue.as_dict() == {"code": "X", "severity": "warning", "deliverable": "Report"}


def _developer_submission(*paths: str) -> dict[str, Any]:
    submission = _submission()
    submission["outputs"] = {
        **submission["outputs"],
        "proposed_files": [
            {"path": path, "language": "python", "purpose": "p", "content": "x = 1\n"}
            for path in paths
        ],
    }
    return submission


def _scoped(submission: dict[str, Any], scope: list[str]) -> deterministic.Report:
    return deterministic.verify(
        deterministic.Subject(
            submission=submission,
            acceptance_criteria=[
                {"id": "c1", "description": "d"},
                {"id": "c2", "description": "e"},
            ],
            deliverables=["Report"],
            output_schema=None,
            configuration={},
            file_scope=scope,
        )
    )


def test_project_tasks_must_propose_files_inside_their_scope() -> None:
    passed = _scoped(_developer_submission("src/a.py", "src/b/c.py"), ["src/"])
    assert passed.passed and ("file_scope", CheckOutcome.PASSED) in passed.checks

    outside = _scoped(_developer_submission("src/a.py", "tests/test_a.py"), ["src/"])
    assert not outside.passed and _codes(outside) == ["FILE_OUTSIDE_SCOPE"]
    assert outside.issues_json() == [
        {"code": "FILE_OUTSIDE_SCOPE", "severity": "error", "location": "1 file(s)"}
    ]
    missing = _scoped(_submission(), ["src/"])
    assert not missing.passed and _codes(missing) == ["FILES_MISSING"]


def test_project_task_files_are_its_deliverables_and_extra_tests_only_warn() -> None:
    submission = _developer_submission("src/a.py")
    submission["outputs"] = {
        "proposed_files": submission["outputs"]["proposed_files"],
        "proposed_tests": [
            {"path": "tests/test_a.py", "language": "python", "purpose": "p", "content": "x"}
        ],
    }  # no text entry for the "Report" deliverable
    report = _scoped(submission, ["src/"])
    assert report.passed
    assert ("deliverables", CheckOutcome.SKIPPED) in report.checks
    assert report.issues_json() == [
        {"code": "TEST_OUTSIDE_SCOPE", "severity": "warning", "location": "1 file(s)"}
    ]


def test_an_integration_fix_that_changes_nothing_is_refused() -> None:
    submission = _developer_submission("src/a.py")
    previous = {"src/a.py": "x = 1\n"}  # the content _developer_submission proposes

    def verify_fix(previous_files: dict[str, str]) -> deterministic.Report:
        return deterministic.verify(
            deterministic.Subject(
                submission=submission,
                acceptance_criteria=[
                    {"id": "c1", "description": "d"},
                    {"id": "c2", "description": "e"},
                ],
                deliverables=["Report"],
                output_schema=None,
                configuration={},
                file_scope=["src/"],
                previous_files=previous_files,
            )
        )

    unchanged = verify_fix(previous)
    assert not unchanged.passed and _codes(unchanged) == ["FIX_UNCHANGED"]
    changed = verify_fix({"src/a.py": "x = 0\n"})
    assert changed.passed and ("fix_changes", CheckOutcome.PASSED) in changed.checks
