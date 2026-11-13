"""The server-side tool gateway (ADR-0019). Agents reach tools only through `ToolGateway.invoke`.

One gateway serves one execution attempt and enforces, on every call:

1. the call limit for the attempt;
2. the tool is in the attempt's contract (granted, enabled and implemented) with enough access;
3. arguments validate against the tool's schema;
4. the result validates against its schema and size limit.

Policy failures (1, 2) are violations: the gateway remembers the first one and the runner fails
the attempt even if the agent catches the error. Every call is recorded with a hash of its
arguments, never the arguments or output. Errors carry fixed, safe messages; tool output is
untrusted data for the agent, never instructions.
"""

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agents.contract import ToolGrant
from app.models import ToolInvocationStatus
from app.tools.registry import REGISTRY, ToolCallContext, ToolDefinition, access_covers

logger = logging.getLogger("agentflow.tools")

# Bounds every attempt, so no agent can loop on tools indefinitely (handoff section 6).
MAX_TOOL_CALLS_PER_ATTEMPT = 50


class ToolError(Exception):
    code = "TOOL_ERROR"
    message = "The tool call failed."
    violation = False

    def __init__(self) -> None:
        super().__init__(self.message)


class ToolPermissionDeniedError(ToolError):
    code = "TOOL_PERMISSION_DENIED"
    message = "The agent called a tool it is not permitted to use."
    violation = True


class ToolCallLimitExceededError(ToolError):
    code = "TOOL_CALL_LIMIT_EXCEEDED"
    message = "The agent exceeded the tool call limit for one attempt."
    violation = True


class ToolArgumentsInvalidError(ToolError):
    code = "TOOL_ARGUMENTS_INVALID"
    message = "The tool arguments are invalid."


class ToolFailedError(ToolError):
    code = "TOOL_FAILED"
    message = "The tool failed."


class ToolOutputInvalidError(ToolError):
    code = "TOOL_OUTPUT_INVALID"
    message = "The tool returned invalid output."


@dataclass(frozen=True)
class InvocationRecord:
    context: ToolCallContext
    tool_name: str
    status: ToolInvocationStatus
    error_code: str | None
    duration_ms: int
    input_hash: str


InvocationRecorder = Callable[[InvocationRecord], uuid.UUID]


def hash_arguments(arguments: object) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=repr)
    return hashlib.sha256(canonical.encode()).hexdigest()


class ToolGateway:
    def __init__(
        self,
        context: ToolCallContext,
        grants: list[ToolGrant],
        record: InvocationRecorder,
        *,
        registry: Mapping[str, ToolDefinition] = REGISTRY,
        max_calls: int = MAX_TOOL_CALLS_PER_ATTEMPT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._context = context
        self._grants = {grant.tool: grant for grant in grants}
        self._record = record
        self._registry = registry
        self._max_calls = max_calls
        self._clock = clock
        self._calls = 0
        # The first policy violation of the attempt; the runner fails the attempt on it.
        self.violation: ToolError | None = None

    def invoke(self, name: str, arguments: Mapping[str, Any]) -> BaseModel:
        """Runs one tool call for the agent. Raises a `ToolError` when refused or failed."""
        started = self._clock()
        input_hash = hash_arguments(arguments)
        try:
            result = self._call(name, arguments)
        except ToolError as error:
            if error.violation and self.violation is None:
                self.violation = error
            status = ToolInvocationStatus.DENIED if error.violation else ToolInvocationStatus.FAILED
            self._write(name, status, error.code, started, input_hash)
            raise
        self._write(name, ToolInvocationStatus.SUCCEEDED, None, started, input_hash)
        return result

    def _call(self, name: str, arguments: Mapping[str, Any]) -> BaseModel:
        self._calls += 1
        if self._calls > self._max_calls:
            raise ToolCallLimitExceededError()
        grant = self._grants.get(name)
        definition = self._registry.get(name)
        if (
            grant is None
            or definition is None
            or definition.implementation is None
            or not definition.available
            or not access_covers(grant.access, definition.requires)
        ):
            raise ToolPermissionDeniedError()
        implementation = definition.implementation
        try:
            parsed = implementation.arguments.model_validate(arguments)
        except ValidationError:  # its text may echo the arguments
            raise ToolArgumentsInvalidError() from None
        try:
            output = implementation.handler(parsed, self._context)
        except ToolError:
            raise
        except Exception as exc:  # tool internals are never shown to the agent or stored
            logger.warning(
                "tool failed",
                extra={
                    "tool": name,
                    "execution_id": str(self._context.execution_id),
                    "error": type(exc).__name__,
                },
            )
            raise ToolFailedError() from None
        try:
            result = implementation.result.model_validate(output.model_dump())
        except (AttributeError, ValidationError):
            raise ToolOutputInvalidError() from None
        if len(result.model_dump_json()) > implementation.max_result_chars:
            raise ToolOutputInvalidError()
        return result

    def _write(
        self,
        name: str,
        status: ToolInvocationStatus,
        error_code: str | None,
        started: float,
        input_hash: str,
    ) -> None:
        self._record(
            InvocationRecord(
                context=self._context,
                tool_name=str(name)[:100],
                status=status,
                error_code=error_code,
                duration_ms=max(0, round((self._clock() - started) * 1000)),
                input_hash=input_hash,
            )
        )
