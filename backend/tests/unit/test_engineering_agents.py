"""Developer and QA runtimes and their structured outputs (Step 3.5, ADR-0022)."""

import json
from typing import Any

import pytest

from app.agents.contract import InvalidSubmissionError
from app.agents.outputs import DeveloperOutputs, ProposedFile, QAOutputs, parse_outputs
from app.agents.specialists import DeveloperAgent, QAAgent, WriterAgent
from app.llm.mock import MockModelAdapter
from tests.unit.test_specialists import _run


def _file(path: str = "src/app.py", **changes: Any) -> dict[str, Any]:
    return {
        "path": path,
        "language": "python",
        "purpose": "Add sync.",
        "content": "x = 1\n",
        **changes,
    }


def _claims(*ids: str) -> list[dict[str, str]]:
    return [{"criterion_id": c, "status": "PARTIAL", "evidence": "Not run here."} for c in ids]


def _developer_reply(**outputs: Any) -> str:
    return json.dumps(
        {
            "summary": "Proposed the change.",
            "outputs": {"Release notes draft": "See proposals.", **outputs},
            "criteria": _claims("c1", "c2"),
        }
    )


def _case(id_: str = "t1", covers: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": id_,
        "title": "Changes are listed",
        "covers": ["c1"] if covers is None else covers,
        "kind": "manual",
        "steps": ["Open the notes."],
        "expected": "Every change appears.",
    }


def _qa_reply(*cases: dict[str, Any]) -> str:
    return json.dumps(
        {
            "summary": "Designed tests.",
            "outputs": {"Release notes draft": "Test plan.", "test_cases": list(cases)},
            "criteria": _claims("c1", "c2"),
        }
    )


@pytest.mark.parametrize(
    "path",
    [
        "src/app.py",
        "tests/test_app.py",
        ".github/workflows/ci.yml",
        ".env.example",
        "a-b/c_d@1+x.ts",
    ],
)
def test_relative_workspace_paths_are_accepted(path: str) -> None:
    assert ProposedFile.model_validate(_file(path)).path == path


@pytest.mark.parametrize(
    "path",
    [
        "../secrets.txt",
        "src/../../etc/passwd",
        "/etc/passwd",
        "C:/Windows/system.ini",
        "src\\app.py",
        "./src/app.py",
        "src//app.py",
        "src/app.py ",
        "my file.py",
        "file.txt:stream",
        ".git/config",
        "home/.ssh/id_rsa",
        "deploy/.aws/credentials",
        ".env",
        "backend/.env.local",
        "/".join(["d"] * 13),
    ],
)
def test_unsafe_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        ProposedFile.model_validate(_file(path))


def test_developer_outputs_need_files_unique_paths_and_text_deliverable_notes() -> None:
    assert parse_outputs(DeveloperOutputs, {"proposed_files": [_file()], "Notes": "ok"})
    invalid: list[dict[str, Any]] = [
        {"proposed_files": []},
        {"proposed_files": [_file()], "proposed_tests": [_file()]},  # the same path twice
        {"proposed_files": [_file()], "Notes": {"nested": True}},
        {"proposed_files": [_file(content="y" * 50_001)]},
        {"proposed_files": [_file(language="Python 3")]},
    ]
    for outputs in invalid:
        assert parse_outputs(DeveloperOutputs, outputs) is None


def test_qa_outputs_need_unique_case_ids() -> None:
    parsed = parse_outputs(QAOutputs, {"test_cases": [_case("t1"), _case("t2", ["c2"])]})
    assert parsed is not None and parsed.covered_criteria() == {"c1", "c2"}
    assert parse_outputs(QAOutputs, {"test_cases": [_case("t1"), _case("t1")]}) is None
    assert parse_outputs(QAOutputs, {"test_cases": []}) is None


@pytest.mark.parametrize(
    ("agent_type", "purpose", "key"),
    [(DeveloperAgent, "agent_development", "proposed_files"), (QAAgent, "agent_qa", "test_cases")],
)
def test_mock_runs_produce_structured_outputs_and_the_platform_note(
    agent_type: Any, purpose: str, key: str
) -> None:
    submission, adapter, recorder, _ = _run(agent_type)

    assert key in submission.outputs
    assert parse_outputs(agent_type.outputs_model, submission.outputs) is not None
    assert submission.limitations[-1] == agent_type.platform_note
    assert recorder.records[0].purpose == purpose
    system = adapter.requests[0].messages[0].content
    assert "The outputs object must also match this JSON Schema" in system
    assert json.dumps(agent_type.outputs_model.model_json_schema(), separators=(",", ":")) in system


def test_other_runtimes_get_no_outputs_schema_or_note() -> None:
    submission, adapter, _, _ = _run(WriterAgent)
    assert "outputs object must also match" not in adapter.requests[0].messages[0].content
    assert not any(item.startswith("Platform note") for item in submission.limitations)


@pytest.mark.parametrize(
    ("agent_type", "purpose", "bad", "good", "code"),
    [
        (
            DeveloperAgent,
            "agent_development",
            _developer_reply(proposed_files=[_file("../../outside.py")]),
            _developer_reply(proposed_files=[_file()]),
            "OUTPUTS_INVALID",
        ),
        (
            QAAgent,
            "agent_qa",
            _qa_reply(_case(covers=["c9"])),
            _qa_reply(_case(), _case("t2", ["c2"])),
            "TEST_CASE_UNKNOWN_CRITERION",
        ),
    ],
)
def test_invalid_outputs_get_one_corrective_retry(
    agent_type: Any, purpose: str, bad: str, good: str, code: str
) -> None:
    adapter = MockModelAdapter()
    adapter.script(purpose, bad, good)

    submission, _, recorder, _ = _run(agent_type, adapter)

    assert len(recorder.records) == 2
    assert code in adapter.requests[1].messages[-1].content
    assert "outside.py" not in adapter.requests[1].messages[-1].content
    assert submission.limitations[-1] == agent_type.platform_note


def test_two_invalid_developer_replies_fail_the_attempt_and_store_nothing() -> None:
    adapter = MockModelAdapter()
    adapter.script("agent_development", _developer_reply(), _developer_reply())  # no files

    with pytest.raises(InvalidSubmissionError) as error:
        _run(DeveloperAgent, adapter)
    assert error.value.issues == ["OUTPUTS_INVALID"]
