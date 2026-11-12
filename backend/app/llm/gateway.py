"""The model gateway (ADR-0013). Every language-model call goes through `ModelGateway.complete`.

- Runs in Celery workers only (synchronous); the API never waits on a model.
- Resolves the model from the request's profile and the configured provider.
- Records one `llm_usage` row per call — succeeded, failed or invalid output — with the usage
  the provider reported. Costs are not computed here (Phase 7 pricing snapshots).
- Treats model output as untrusted: structured output must validate against the requested
  Pydantic schema, or the call fails with ModelOutputInvalidError.
- Logs metadata only; prompts and outputs are never logged or stored in the usage record.
"""

import logging
import os
import time
import uuid
from collections.abc import Callable
from functools import partial

from pydantic import BaseModel, ValidationError

from app.core.config import Settings, get_settings
from app.db.sync_session import get_worker_session_factory
from app.llm.mock import MockModelAdapter
from app.llm.types import CallContext, ModelAdapter, ModelRequest, ModelResponse, ProviderUsage
from app.models import LlmCallStatus
from app.services.usage import CallRecord, record_model_call

logger = logging.getLogger("agentflow.llm")


class ModelGatewayError(Exception):
    code = "MODEL_ERROR"


class UnknownModelProfileError(ModelGatewayError):
    code = "UNKNOWN_MODEL_PROFILE"


class ModelCallFailedError(ModelGatewayError):
    code = "MODEL_CALL_FAILED"


class ModelOutputInvalidError(ModelGatewayError):
    code = "MODEL_OUTPUT_INVALID"


def create_adapter(settings: Settings) -> ModelAdapter:
    """The adapter for MODEL_PROVIDER. Real providers arrive behind LiteLLM in Phase 3."""
    adapters: dict[str, Callable[[], ModelAdapter]] = {"mock": MockModelAdapter}
    return adapters[settings.model_provider]()


UsageRecorder = Callable[[CallRecord], uuid.UUID]


class ModelGateway:
    def __init__(
        self,
        settings: Settings,
        adapter: ModelAdapter,
        record_usage: UsageRecorder,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`record_usage` persists one call record; workers bind it to their session factory."""
        self._settings = settings
        self._adapter = adapter
        self._record_usage = record_usage
        self._clock = clock

    def resolve_model(self, profile: str) -> str:
        # One configured model until per-profile routing exists (Phase 3).
        if profile != "default":
            raise UnknownModelProfileError(f"unknown model profile {profile!r}")
        return self._settings.model_name

    def complete[T: BaseModel](
        self,
        context: CallContext,
        request: ModelRequest,
        response_format: type[T] | None = None,
    ) -> ModelResponse[T]:
        model = self.resolve_model(request.profile)
        provider = self._adapter.provider
        started = self._clock()

        def record(
            status: LlmCallStatus,
            usage: ProviderUsage | None,
            provider_request_id: str | None,
            error_code: str | None,
        ) -> CallRecord:
            return CallRecord(
                context=context,
                purpose=request.purpose,
                provider=provider,
                model=model,
                status=status,
                usage=usage,
                provider_request_id=provider_request_id,
                error_code=error_code,
                latency_ms=max(0, round((self._clock() - started) * 1000)),
            )

        try:
            result = self._adapter.complete(request, model=model, response_format=response_format)
        except Exception as exc:
            entry = record(LlmCallStatus.FAILED, None, None, ModelCallFailedError.code)
            self._record_usage(entry)
            self._log(entry, error=type(exc).__name__)
            raise ModelCallFailedError(f"{provider} call failed for {request.purpose!r}") from exc

        parsed: T | None = None
        if response_format is not None:
            try:
                parsed = response_format.model_validate_json(result.text)
            except ValidationError as exc:
                entry = record(
                    LlmCallStatus.INVALID_OUTPUT,
                    result.usage,
                    result.provider_request_id,
                    ModelOutputInvalidError.code,
                )
                self._record_usage(entry)
                self._log(entry, error="ValidationError")
                # The message names the schema only; it never echoes the model's output.
                raise ModelOutputInvalidError(
                    f"output for {request.purpose!r} does not match {response_format.__name__}"
                ) from exc

        entry = record(LlmCallStatus.SUCCEEDED, result.usage, result.provider_request_id, None)
        usage_id = self._record_usage(entry)
        self._log(entry)
        return ModelResponse(
            text=result.text,
            parsed=parsed,
            usage=result.usage,
            provider=provider,
            model=model,
            usage_id=usage_id,
            provider_request_id=result.provider_request_id,
        )

    @staticmethod
    def _log(entry: CallRecord, error: str | None = None) -> None:
        usage = entry.usage
        logger.info(
            "model call",
            extra={
                "purpose": entry.purpose,
                "provider": entry.provider,
                "model": entry.model,
                "status": entry.status.value,
                "latency_ms": entry.latency_ms,
                "input_tokens": usage.input_tokens if usage else None,
                "output_tokens": usage.output_tokens if usage else None,
                "workspace_id": str(entry.context.workspace_id),
                "workflow_id": str(entry.context.workflow_id)
                if entry.context.workflow_id
                else None,
                "task_id": str(entry.context.task_id) if entry.context.task_id else None,
                "error": error,
            },
        )


_process_gateway: dict[int, ModelGateway] = {}


def get_model_gateway() -> ModelGateway:
    """The gateway of the current worker process, created lazily (like its DB engine)."""
    pid = os.getpid()
    if pid not in _process_gateway:
        settings = get_settings()
        _process_gateway.clear()
        recorder = partial(record_model_call, get_worker_session_factory())
        _process_gateway[pid] = ModelGateway(settings, create_adapter(settings), recorder)
    return _process_gateway[pid]
