"""The built-in model-backed agents: Research, Analyst, Writer and General (ADR-0021), and
Developer and QA (ADR-0022).

Each runtime makes one structured model call through the model gateway per try, with its
versioned instructions (`app/agents/instructions.py`) and the task contract inside delimiters.
An invalid reply (bad JSON, or criterion claims that do not match the contract) gets exactly one
corrective retry naming only the issue codes; a second invalid reply fails the attempt.

The runtime for an agent version is chosen from its `system_instruction_ref`, never from task or
user input. Versions without a runtime (custom agents whose instructions cannot be resolved)
fail the attempt with AGENT_RUNTIME_UNAVAILABLE.

Runtimes whose tools do not exist yet append a platform note to every submission, whatever the
model wrote: Research (no retrieval), Developer (nothing executed or written) and QA (no tests
run). Developer and QA outputs must also fit their stricter models (`app/agents/outputs.py`).
"""

import json
import re
from typing import Any, ClassVar

from pydantic import BaseModel

from app.agents.base import AgentContext, BaseAgent
from app.agents.contract import (
    AgentSubmission,
    CriterionClaim,
    CriterionStatus,
    InvalidSubmissionError,
    TaskContract,
)
from app.agents.instructions import COMMON_RULES, INSTRUCTIONS, Instruction
from app.agents.outputs import DeveloperOutputs, QAOutputs, parse_outputs
from app.llm.gateway import ModelOutputInvalidError
from app.llm.mock import register_default
from app.llm.types import CallContext, ChatMessage, ModelRequest
from app.schemas.agents import AgentConfiguration

_CONTRACT = re.compile(r"<task_contract>\n(.*?)\n</task_contract>", re.S)
# Limitation items are capped at 20 by AgentSubmission.
_MAX_ITEMS = 20


class AgentRuntimeUnavailableError(Exception):
    code = "AGENT_RUNTIME_UNAVAILABLE"
    message = "No runtime is available for this agent version yet."

    def __init__(self) -> None:
        super().__init__(self.message)


def _contract_json(contract: TaskContract) -> str:
    """The parts of the contract the model needs. `</` is escaped as `<\\/` (still valid JSON),
    so no value can close the delimiter."""
    data = contract.model_dump(
        mode="json",
        include={
            "objective",
            "context",
            "input_references",
            "required_skills",
            "deliverables",
            "output_schema",
            "acceptance_criteria",
            "allowed_tools",
            "security_classification",
        },
    )
    data["dependencies_completed"] = len(contract.dependencies)
    return json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/")


def _schema(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema(), separators=(",", ":"))


def build_request(
    instruction: Instruction,
    contract: TaskContract,
    configuration: AgentConfiguration,
    correction: list[str] | None = None,
    outputs_model: type[BaseModel] | None = None,
) -> ModelRequest:
    criteria = ", ".join(c.id for c in contract.acceptance_criteria) or "none"
    system = (
        f"{instruction.text}\n\n{COMMON_RULES}\n"
        f"Acceptance criterion ids to claim: {criteria}.\n"
        f"JSON Schema:\n{_schema(AgentSubmission)}"
    )
    if outputs_model is not None:
        system += (
            f"\nThe outputs object must also match this JSON Schema:\n{_schema(outputs_model)}"
        )
    messages = [
        ChatMessage(role="system", content=system),
        ChatMessage(
            role="user", content=f"<task_contract>\n{_contract_json(contract)}\n</task_contract>"
        ),
    ]
    if correction:
        messages.append(
            ChatMessage(
                role="user",
                content=(
                    f"Your previous reply was rejected ({', '.join(correction)}). Reply again with "
                    f"only one JSON object matching the schema, with exactly one claim for each "
                    f"criterion id: {criteria}."
                ),
            )
        )
    budget = contract.token_budget
    max_tokens = configuration.model.max_output_tokens
    return ModelRequest(
        purpose=instruction.purpose,
        messages=messages,
        profile=configuration.model.profile,
        temperature=configuration.model.temperature,
        max_output_tokens=min(max_tokens, budget) if budget is not None else max_tokens,
    )


class ModelAgent(BaseAgent):
    instruction_ref: ClassVar[str]
    # A stricter shape for `outputs`, sent to the model and checked before accepting a reply.
    outputs_model: ClassVar[type[BaseModel] | None] = None
    # Appended to every submission unless the contract grants one of `note_unless_tools`.
    platform_note: ClassVar[str | None] = None
    note_unless_tools: ClassVar[frozenset[str]] = frozenset()

    def execute(self, contract: TaskContract, context: AgentContext) -> AgentSubmission:
        instruction = INSTRUCTIONS[self.instruction_ref]
        call = CallContext(
            workspace_id=contract.workspace_id,
            workflow_id=contract.workflow_id,
            task_id=contract.task_id,
            execution_id=context.execution_id,
        )
        issues: list[str] | None = None
        for _ in range(2):  # the first try and one corrective retry
            request = build_request(
                instruction, contract, self.configuration, issues, self.outputs_model
            )
            try:
                response = context.models.complete(call, request, AgentSubmission)
            except ModelOutputInvalidError:  # recorded by the gateway; never echoed back
                issues = ["SCHEMA_INVALID"]
                continue
            validation = self.validate_output(contract, response.parsed)
            if validation.submission is not None and not validation.issues:
                extra = self.output_issues(contract, validation.submission)
                if not extra:
                    return self.finalise(contract, validation.submission)
                issues = extra
            else:
                issues = validation.issues
        raise InvalidSubmissionError(issues or ["SCHEMA_INVALID"])

    def output_issues(self, contract: TaskContract, submission: AgentSubmission) -> list[str]:
        """Runtime-specific checks on top of the platform's (which agents cannot weaken)."""
        if self.outputs_model is None:
            return []
        if parse_outputs(self.outputs_model, submission.outputs) is None:
            return ["OUTPUTS_INVALID"]
        return []

    def finalise(self, contract: TaskContract, submission: AgentSubmission) -> AgentSubmission:
        """Appends the platform note, once and last, whatever the model wrote."""
        note = self.platform_note
        if note is None or any(g.tool in self.note_unless_tools for g in contract.allowed_tools):
            return submission
        kept = [item for item in submission.limitations if item != note][: _MAX_ITEMS - 1]
        return submission.model_copy(update={"limitations": [*kept, note]})


class ResearchAgent(ModelAgent):
    instruction_ref = "builtin://researcher/v1"
    platform_note = (
        "Platform note: no search or document tools were available, so findings rely on the "
        "model's own knowledge and are unverified."
    )
    note_unless_tools = frozenset({"web_search", "document_retrieval"})
    NOTE = platform_note


class AnalystAgent(ModelAgent):
    instruction_ref = "builtin://analyst/v1"


class WriterAgent(ModelAgent):
    instruction_ref = "builtin://writer/v1"


class GeneralAgent(ModelAgent):
    instruction_ref = "builtin://general/v1"


class DeveloperAgent(ModelAgent):
    instruction_ref = "builtin://developer/v1"
    outputs_model = DeveloperOutputs
    platform_note = (
        "Platform note: no code was executed and no files were written; the proposed files and "
        "tests are unverified."
    )
    note_unless_tools = frozenset({"workspace_files_write", "test_runner"})


class QAAgent(ModelAgent):
    instruction_ref = "builtin://qa/v1"
    outputs_model = QAOutputs
    platform_note = (
        "Platform note: no tests were executed; the test cases are designs only and no results "
        "were observed."
    )
    note_unless_tools = frozenset({"test_runner"})

    def output_issues(self, contract: TaskContract, submission: AgentSubmission) -> list[str]:
        outputs = parse_outputs(QAOutputs, submission.outputs)
        if outputs is None:
            return ["OUTPUTS_INVALID"]
        known = {criterion.id for criterion in contract.acceptance_criteria}
        return [] if outputs.covered_criteria() <= known else ["TEST_CASE_UNKNOWN_CRITERION"]


RUNTIMES: dict[str, type[BaseAgent]] = {
    agent.instruction_ref: agent
    for agent in (
        ResearchAgent,
        AnalystAgent,
        WriterAgent,
        GeneralAgent,
        DeveloperAgent,
        QAAgent,
    )
}


def runtime_for(configuration: AgentConfiguration) -> type[BaseAgent]:
    runtime = RUNTIMES.get(configuration.system_instruction_ref)
    if runtime is None:
        raise AgentRuntimeUnavailableError()
    return runtime


# --- deterministic mock defaults (MODEL_PROVIDER=mock) -------------------------------------------


def _mock_developer_outputs(contract: dict[str, Any]) -> dict[str, Any]:
    purpose = f"Mock proposal for: {str(contract.get('objective', 'the task'))[:200]}"
    content = "# Mock proposal: no real model was used.\n"
    return {
        "proposed_files": [
            {
                "path": "proposals/mock_change.py",
                "language": "python",
                "purpose": purpose,
                "content": content,
            }
        ],
        "proposed_tests": [
            {
                "path": "tests/test_mock_change.py",
                "language": "python",
                "purpose": purpose,
                "content": content,
            }
        ],
    }


def _mock_qa_outputs(contract: dict[str, Any]) -> dict[str, Any]:
    criteria = [criterion["id"] for criterion in contract.get("acceptance_criteria", [])]
    return {
        "test_cases": [
            {
                "id": f"t{index}",
                "title": f"Mock check of {criterion}",
                "covers": [criterion],
                "kind": "manual",
                "steps": ["Mock step: no real model was used."],
                "expected": "Mock expectation.",
            }
            for index, criterion in enumerate(criteria, start=1)
        ]
        or [
            {
                "id": "t1",
                "title": "Mock check",
                "covers": [],
                "kind": "manual",
                "steps": ["Mock step: no real model was used."],
                "expected": "Mock expectation.",
            }
        ]
    }


_MOCK_OUTPUTS = {"development": _mock_developer_outputs, "qa": _mock_qa_outputs}


def _mock_submission(role: str) -> Any:
    def reply(request: ModelRequest) -> str:
        """A schema-valid submission derived from the delimited contract, so development and
        E2E runs work without a model. Tests that need exact replies script them instead."""
        contract: dict[str, Any] = {}
        for message in request.messages:
            match = _CONTRACT.search(message.content)
            if match:
                contract = json.loads(match.group(1))
        objective = str(contract.get("objective", "the task"))[:300]
        return AgentSubmission(
            summary=f"Mock {role} result for task '{objective}'.",
            outputs={
                **{name: f"Mock content for: {name}" for name in contract.get("deliverables", [])},
                **(_MOCK_OUTPUTS[role](contract) if role in _MOCK_OUTPUTS else {}),
            },
            criteria=[
                CriterionClaim(
                    criterion_id=criterion["id"],
                    status=CriterionStatus.MET,
                    evidence="Mock model claim; no real work was performed.",
                )
                for criterion in contract.get("acceptance_criteria", [])
            ],
            limitations=["Mock model provider: no real model was used."],
        ).model_dump_json()

    return reply


for _instruction in INSTRUCTIONS.values():
    register_default(_instruction.purpose, _mock_submission(_instruction.role))
