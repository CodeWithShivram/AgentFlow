"""Task contract, submission validation and the mock agent (Step 3.1, ADR-0018)."""

import uuid
from typing import Any

import pytest

from app.agents.base import AgentContext
from app.agents.contract import (
    MAX_OUTPUTS_CHARS,
    AgentSubmission,
    CriterionStatus,
    TaskContract,
    validate_submission,
)
from app.agents.mock import MockAgent
from app.execution import runner
from app.llm.gateway import ModelGateway
from app.llm.mock import MockModelAdapter
from app.schemas.agents import AgentConfiguration
from app.tools.gateway import InvocationRecord, ToolGateway
from app.tools.registry import ToolCallContext
from tests.conftest import make_settings

CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "system_instruction_ref": "builtin://researcher/v1",
    "capabilities": ["research", "evidence_summary"],
    "tools": [{"tool": "web_search"}, {"tool": "python_analysis"}],
    "limits": {"timeout_seconds": 3600, "max_tokens_per_task": 5000},
}


def _claim(**changes: Any) -> runner.Claim:
    values: dict[str, Any] = {
        "execution_id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "workflow_id": uuid.uuid4(),
        "task_id": uuid.uuid4(),
        "attempt_number": 2,
        "dispatch_event_id": uuid.uuid4(),
        "agent_id": uuid.uuid4(),
        "agent_version_id": uuid.uuid4(),
        "agent_version": 3,
        "configuration": CONFIG,
        "title": "Compare vendors",
        "description": "Three vendors.",
        "input_references": [],
        "required_skills": ["research"],
        "deliverables": ["Comparison table"],
        "output_schema": None,
        "acceptance_criteria": [
            {"id": "c1", "description": "Covers all vendors."},
            {"id": "c2", "description": "Cites sources."},
        ],
        "dependencies": [],
    }
    return runner.Claim(**{**values, **changes})


def _no_records(record: InvocationRecord) -> uuid.UUID:
    raise AssertionError("the mock agent calls no tools")


def _no_model() -> ModelGateway:
    """A gateway for agents that never call a model: any call fails (no mock replies)."""
    return ModelGateway(make_settings(), MockModelAdapter(defaults={}), lambda record: uuid.uuid4())


def _contract(**changes: Any) -> TaskContract:
    return runner.build_contract(_claim(**changes), AgentConfiguration.model_validate(CONFIG))


def _submission(**changes: Any) -> dict[str, Any]:
    return {
        "summary": "Done.",
        "criteria": [
            {"criterion_id": "c1", "status": "MET", "evidence": "Table rows 1-3."},
            {"criterion_id": "c2", "status": "PARTIAL", "evidence": "Two sources."},
        ],
        **changes,
    }


def test_contract_grants_no_tools_until_they_are_implemented_and_caps_the_timeout() -> None:
    contract = _contract()
    # web_search is granted and enabled, but has no implementation yet (ADR-0019);
    # python_analysis is granted but code execution is disabled on the platform.
    assert contract.allowed_tools == []
    assert contract.timeout_seconds == runner.AGENT_TIME_LIMIT_SECONDS
    assert contract.token_budget == 5000
    assert contract.retry_policy.max_attempts == runner.MAX_ATTEMPTS_PER_TASK
    assert contract.retry_policy.attempt_number == 2
    assert contract.security_classification == "INTERNAL"


def test_contract_rejects_malformed_stored_criteria() -> None:
    with pytest.raises(ValueError):
        _contract(acceptance_criteria=[{"id": "Not A Slug", "description": "x"}])


def test_a_complete_submission_is_valid() -> None:
    result = validate_submission(_contract(), _submission())
    assert result.valid and result.issues == []
    assert result.submission is not None
    assert result.submission.criteria[1].status == CriterionStatus.PARTIAL


@pytest.mark.parametrize(
    ("criteria", "issues"),
    [
        ([("c1", "MET")], ["MISSING_CRITERION_CLAIM"]),
        ([("c1", "MET"), ("c2", "MET"), ("c9", "MET")], ["UNKNOWN_CRITERION"]),
        ([("c1", "MET"), ("c1", "NOT_MET"), ("c2", "MET")], ["DUPLICATE_CRITERION_CLAIM"]),
        ([], ["MISSING_CRITERION_CLAIM"]),
    ],
)
def test_criterion_claims_must_match_the_contract(
    criteria: list[tuple[str, str]], issues: list[str]
) -> None:
    claims = [{"criterion_id": c, "status": s, "evidence": "e"} for c, s in criteria]
    result = validate_submission(_contract(), _submission(criteria=claims))
    assert not result.valid and result.issues == issues


@pytest.mark.parametrize(
    "result",
    [
        "not a submission",
        _submission(summary=""),
        _submission(extra="field"),
        _submission(schema_version=2),
        _submission(summary="key sk-" + "a" * 40),  # credential-like text is never stored
        _submission(outputs={"blob": "x" * MAX_OUTPUTS_CHARS}),
        _submission(assumptions=["a"] * 21),
        _submission(criteria=[{"criterion_id": "c1", "status": "DONE", "evidence": "e"}]),
    ],
)
def test_malformed_output_is_schema_invalid_without_echoing_it(result: object) -> None:
    validation = validate_submission(_contract(), result)
    assert validation.submission is None and validation.issues == ["SCHEMA_INVALID"]


def test_the_mock_agent_returns_a_valid_deterministic_submission() -> None:
    claim = _claim()
    agent = MockAgent(AgentConfiguration.model_validate(CONFIG))
    contract = runner.build_contract(claim, agent.configuration)
    context = AgentContext(
        execution_id=claim.execution_id,
        dispatch_event_id=claim.dispatch_event_id,
        agent_id=claim.agent_id,
        agent_version_id=claim.agent_version_id,
        agent_version=claim.agent_version,
        tools=ToolGateway(
            ToolCallContext(
                claim.workspace_id, claim.workflow_id, claim.task_id, claim.execution_id
            ),
            contract.allowed_tools,
            _no_records,
        ),
        models=_no_model(),
    )

    first = agent.execute(contract, context)
    assert first == agent.execute(contract, context)
    assert agent.validate_output(contract, first).valid
    assert first.summary == "Mock result for task 'Compare vendors' (agent version 3, attempt 2)."
    assert first.outputs == {"Comparison table": "Mock content for: Comparison table"}
    assert (
        runner.run_agent(claim, MockAgent, models=_no_model(), record_tool_call=_no_records)
        == first
    )

    capabilities = agent.get_capabilities()
    assert capabilities.capabilities == {"research", "evidence_summary"}
    assert capabilities.tools == {"web_search", "python_analysis"}


def test_submissions_round_trip_through_storage() -> None:
    submission = AgentSubmission.model_validate(_submission(open_questions=["Which region?"]))
    stored = submission.model_dump(mode="json")
    assert AgentSubmission.model_validate(stored) == submission
