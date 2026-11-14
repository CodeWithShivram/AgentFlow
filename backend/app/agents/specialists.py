"""The built-in model-backed agents: Research, Analyst, Writer and General (ADR-0021).

Each runtime makes one structured model call through the model gateway per try, with its
versioned instructions (`app/agents/instructions.py`) and the task contract inside delimiters.
An invalid reply (bad JSON, or criterion claims that do not match the contract) gets exactly one
corrective retry naming only the issue codes; a second invalid reply fails the attempt.

The runtime for an agent version is chosen from its `system_instruction_ref`, never from task or
user input. Versions without a runtime (Developer and QA until Step 3.5, custom agents whose
instructions cannot be resolved) fail the attempt with AGENT_RUNTIME_UNAVAILABLE.
"""

import json
import re
from typing import Any, ClassVar

from app.agents.base import AgentContext, BaseAgent
from app.agents.contract import (
    AgentSubmission,
    CriterionClaim,
    CriterionStatus,
    InvalidSubmissionError,
    TaskContract,
)
from app.agents.instructions import COMMON_RULES, INSTRUCTIONS, Instruction
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


def build_request(
    instruction: Instruction,
    contract: TaskContract,
    configuration: AgentConfiguration,
    correction: list[str] | None = None,
) -> ModelRequest:
    criteria = ", ".join(c.id for c in contract.acceptance_criteria) or "none"
    system = (
        f"{instruction.text}\n\n{COMMON_RULES}\n"
        f"Acceptance criterion ids to claim: {criteria}.\n"
        f"JSON Schema:\n{json.dumps(AgentSubmission.model_json_schema(), separators=(',', ':'))}"
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
            request = build_request(instruction, contract, self.configuration, issues)
            try:
                response = context.models.complete(call, request, AgentSubmission)
            except ModelOutputInvalidError:  # recorded by the gateway; never echoed back
                issues = ["SCHEMA_INVALID"]
                continue
            validation = self.validate_output(contract, response.parsed)
            if validation.submission is not None and not validation.issues:
                return self.finalise(contract, validation.submission)
            issues = validation.issues
        raise InvalidSubmissionError(issues or ["SCHEMA_INVALID"])

    def finalise(self, contract: TaskContract, submission: AgentSubmission) -> AgentSubmission:
        return submission


class ResearchAgent(ModelAgent):
    instruction_ref = "builtin://researcher/v1"
    NOTE = (
        "Platform note: no search or document tools were available, so findings rely on the "
        "model's own knowledge and are unverified."
    )

    def finalise(self, contract: TaskContract, submission: AgentSubmission) -> AgentSubmission:
        """Says so on every submission until retrieval tools exist, whatever the model wrote."""
        retrieval = {"web_search", "document_retrieval"}
        if any(grant.tool in retrieval for grant in contract.allowed_tools):
            return submission
        kept = [item for item in submission.limitations if item != self.NOTE][: _MAX_ITEMS - 1]
        return submission.model_copy(update={"limitations": [*kept, self.NOTE]})


class AnalystAgent(ModelAgent):
    instruction_ref = "builtin://analyst/v1"


class WriterAgent(ModelAgent):
    instruction_ref = "builtin://writer/v1"


class GeneralAgent(ModelAgent):
    instruction_ref = "builtin://general/v1"


RUNTIMES: dict[str, type[BaseAgent]] = {
    agent.instruction_ref: agent
    for agent in (ResearchAgent, AnalystAgent, WriterAgent, GeneralAgent)
}


def runtime_for(configuration: AgentConfiguration) -> type[BaseAgent]:
    runtime = RUNTIMES.get(configuration.system_instruction_ref)
    if runtime is None:
        raise AgentRuntimeUnavailableError()
    return runtime


# --- deterministic mock defaults (MODEL_PROVIDER=mock) -------------------------------------------


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
                name: f"Mock content for: {name}" for name in contract.get("deliverables", [])
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
