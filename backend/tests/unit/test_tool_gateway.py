"""Tool registry and the server-side tool gateway (Step 3.2, ADR-0019)."""

import hashlib
import json
import uuid
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.agents.base import AgentContext
from app.agents.catalog import TOOLS
from app.agents.contract import AgentSubmission, TaskContract, ToolGrant
from app.agents.mock import MockAgent
from app.execution import runner
from app.models import ToolInvocationStatus
from app.schemas.agents import AgentConfiguration
from app.tools.gateway import (
    MAX_TOOL_CALLS_PER_ATTEMPT,
    InvocationRecord,
    ToolArgumentsInvalidError,
    ToolCallLimitExceededError,
    ToolFailedError,
    ToolGateway,
    ToolOutputInvalidError,
    ToolPermissionDeniedError,
    hash_arguments,
)
from app.tools.registry import (
    REGISTRY,
    ToolCallContext,
    ToolDefinition,
    ToolImplementation,
    usable_grants,
)


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=100)


class Answer(BaseModel):
    text: str


def _echo(arguments: Query, context: ToolCallContext) -> Answer:
    return Answer(text=f"echo {arguments.text}")


def _broken(arguments: Query, context: ToolCallContext) -> Answer:
    raise RuntimeError("secret internal detail sk-not-for-agents")


def _wrong_type(arguments: Query, context: ToolCallContext) -> Any:
    return {"text": "a plain dict, not the declared model"}


def _huge(arguments: Query, context: ToolCallContext) -> Answer:
    return Answer(text="x" * 200)


def _tool(name: str, requires: Any, handler: Any, **options: Any) -> ToolDefinition:
    return ToolDefinition(name, requires, ToolImplementation(Query, Answer, handler, **options))


# Catalog names with test implementations. python_analysis stays disabled by the catalog.
TEST_REGISTRY = {
    "web_search": _tool("web_search", "read", _echo),
    "artifact_writer": _tool("artifact_writer", "write", _echo),
    "document_retrieval": _tool("document_retrieval", "read", _broken),
    "workspace_files_read": _tool("workspace_files_read", "read", _wrong_type),
    "workspace_files_write": _tool("workspace_files_write", "write", _huge, max_result_chars=50),
    "python_analysis": _tool("python_analysis", "write", _echo),
}
CONTEXT = ToolCallContext(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())


class FakeClock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        self.now += 0.25
        return self.now


def _gateway(
    *grants: tuple[str, str], **options: Any
) -> tuple[ToolGateway, list[InvocationRecord]]:
    records: list[InvocationRecord] = []

    def record(entry: InvocationRecord) -> uuid.UUID:
        records.append(entry)
        return uuid.uuid4()

    gateway = ToolGateway(
        CONTEXT,
        [ToolGrant(tool=tool, access=access) for tool, access in grants],
        record,
        registry=TEST_REGISTRY,
        clock=FakeClock(),
        **options,
    )
    return gateway, records


def test_the_registry_covers_the_catalog_and_nothing_runs_yet() -> None:
    assert set(REGISTRY) == set(TOOLS)
    assert not any(definition.available for definition in REGISTRY.values())


def test_contracts_only_list_grants_that_can_run_with_enough_access() -> None:
    configuration = AgentConfiguration.model_validate(
        {
            "system_instruction_ref": "builtin://x/v1",
            "capabilities": ["research"],
            "tools": [
                {"tool": "web_search", "access": "read"},
                {"tool": "artifact_writer", "access": "read"},  # needs write
                {"tool": "python_analysis", "access": "write"},  # disabled by the catalog
                {"tool": "document_retrieval", "access": "write"},  # write covers read
                {"tool": "test_runner", "access": "write"},  # not implemented
            ],
        }
    )
    assert usable_grants(configuration, TEST_REGISTRY) == [
        ToolGrant(tool="web_search", access="read"),
        ToolGrant(tool="document_retrieval", access="write"),
    ]
    assert usable_grants(configuration) == []


def test_a_permitted_call_returns_the_validated_result_and_records_only_a_hash() -> None:
    gateway, records = _gateway(("web_search", "read"))

    result = gateway.invoke("web_search", {"text": "pricing"})

    assert result == Answer(text="echo pricing")
    (record,) = records
    assert record.status == ToolInvocationStatus.SUCCEEDED and record.error_code is None
    assert record.tool_name == "web_search" and record.duration_ms == 250
    assert record.context == CONTEXT
    expected = hashlib.sha256(json.dumps({"text": "pricing"}).replace(" ", "").encode())
    assert record.input_hash == expected.hexdigest()
    assert "pricing" not in repr(record)
    assert gateway.violation is None


def test_argument_hashes_ignore_key_order() -> None:
    assert hash_arguments({"a": 1, "b": [2]}) == hash_arguments({"b": [2], "a": 1})
    assert hash_arguments({"a": 1}) != hash_arguments({"a": 2})


@pytest.mark.parametrize(
    ("grants", "name"),
    [
        ((), "web_search"),  # not granted to this attempt
        ((("web_search", "read"),), "rm -rf /"),  # unknown tool
        ((("artifact_writer", "read"),), "artifact_writer"),  # read grant on a write tool
        ((("python_analysis", "write"),), "python_analysis"),  # disabled on the platform
        ((("test_runner", "write"),), "test_runner"),  # not in this registry
    ],
)
def test_calls_outside_the_contract_are_denied_and_recorded_as_violations(
    grants: tuple[tuple[str, str], ...], name: str
) -> None:
    gateway, records = _gateway(*grants)

    with pytest.raises(ToolPermissionDeniedError) as error:
        gateway.invoke(name, {"text": "x"})

    assert error.value.code == "TOOL_PERMISSION_DENIED"
    assert gateway.violation is error.value
    (record,) = records
    assert record.status == ToolInvocationStatus.DENIED
    assert record.error_code == "TOOL_PERMISSION_DENIED" and record.tool_name == name


def test_the_first_violation_is_kept() -> None:
    gateway, _ = _gateway(("web_search", "read"), max_calls=1)
    with pytest.raises(ToolPermissionDeniedError) as first:
        gateway.invoke("artifact_writer", {"text": "x"})
    with pytest.raises(ToolCallLimitExceededError):
        gateway.invoke("web_search", {"text": "x"})
    assert gateway.violation is first.value


def test_the_call_limit_bounds_every_attempt() -> None:
    assert MAX_TOOL_CALLS_PER_ATTEMPT == 50
    gateway, records = _gateway(("web_search", "read"), max_calls=2)
    gateway.invoke("web_search", {"text": "1"})
    gateway.invoke("web_search", {"text": "2"})

    with pytest.raises(ToolCallLimitExceededError):
        gateway.invoke("web_search", {"text": "3"})

    assert [r.status for r in records] == [
        ToolInvocationStatus.SUCCEEDED,
        ToolInvocationStatus.SUCCEEDED,
        ToolInvocationStatus.DENIED,
    ]
    assert records[-1].error_code == "TOOL_CALL_LIMIT_EXCEEDED"
    assert isinstance(gateway.violation, ToolCallLimitExceededError)


@pytest.mark.parametrize(
    ("grant", "arguments", "error_type"),
    [
        (("web_search", "read"), {"text": "y" * 101}, ToolArgumentsInvalidError),
        (("web_search", "read"), {"text": "x", "extra": True}, ToolArgumentsInvalidError),
        (("web_search", "read"), ["not", "a", "mapping"], ToolArgumentsInvalidError),
        (("document_retrieval", "read"), {"text": "x"}, ToolFailedError),
        (("workspace_files_read", "read"), {"text": "x"}, ToolOutputInvalidError),
        (("workspace_files_write", "write"), {"text": "x"}, ToolOutputInvalidError),
    ],
)
def test_failed_calls_raise_safe_errors_and_are_not_violations(
    grant: tuple[str, str], arguments: Any, error_type: type[Exception]
) -> None:
    gateway, records = _gateway(grant)

    with pytest.raises(error_type) as error:
        gateway.invoke(grant[0], arguments)

    # Fixed messages: never argument values, tool internals or output.
    assert "sk-not-for-agents" not in str(error.value) and "yyy" not in str(error.value)
    assert error.value.__cause__ is None
    (record,) = records
    assert record.status == ToolInvocationStatus.FAILED
    assert record.error_code == error.value.code  # type: ignore[attr-defined]
    assert gateway.violation is None


# --- the runner enforces violations on the attempt -----------------------------------------------


class _ProbingAgent(MockAgent):
    """Uses its permitted tool, then probes for another one and swallows the refusal."""

    def execute(self, contract: TaskContract, context: AgentContext) -> AgentSubmission:
        context.tools.invoke("web_search", {"text": "ok"})
        try:
            context.tools.invoke("artifact_writer", {"text": "probe"})
        except ToolPermissionDeniedError:
            pass
        return super().execute(contract, context)


class _SearchingAgent(MockAgent):
    def execute(self, contract: TaskContract, context: AgentContext) -> AgentSubmission:
        answer = context.tools.invoke("web_search", {"text": contract.objective[:100]})
        submission = super().execute(contract, context)
        return submission.model_copy(update={"outputs": answer.model_dump()})


def _claim() -> runner.Claim:
    return runner.Claim(
        execution_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        attempt_number=1,
        dispatch_event_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        agent_version_id=uuid.uuid4(),
        agent_version=1,
        configuration={
            "system_instruction_ref": "builtin://researcher/v1",
            "capabilities": ["research"],
            "tools": [{"tool": "web_search"}],
        },
        title="Compare vendors",
        description="",
        input_references=[],
        required_skills=["research"],
        deliverables=[],
        output_schema=None,
        acceptance_criteria=[],
        dependencies=[],
    )


def test_a_swallowed_violation_still_fails_the_attempt() -> None:
    records: list[InvocationRecord] = []

    def record(entry: InvocationRecord) -> uuid.UUID:
        records.append(entry)
        return uuid.uuid4()

    claim = _claim()
    with pytest.raises(ToolPermissionDeniedError):
        runner.run_agent(claim, _ProbingAgent, registry=TEST_REGISTRY, record_tool_call=record)

    assert [(r.tool_name, r.status) for r in records] == [
        ("web_search", ToolInvocationStatus.SUCCEEDED),
        ("artifact_writer", ToolInvocationStatus.DENIED),
    ]
    assert {r.context.execution_id for r in records} == {claim.execution_id}


def test_an_agent_using_its_permitted_tool_submits_normally() -> None:
    claim = _claim()
    submission = runner.run_agent(
        claim, _SearchingAgent, registry=TEST_REGISTRY, record_tool_call=lambda r: uuid.uuid4()
    )
    assert submission.outputs == {"text": "echo Compare vendors"}
