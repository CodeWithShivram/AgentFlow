"""LiteLLM adapter behind the model gateway (Step 3.3, ADR-0020). Fully offline: LiteLLM's
`mock_response` returns a provider-shaped reply without any network call or real key."""

import logging
import os
import uuid
from typing import Any

import litellm
import pytest
from litellm.exceptions import RateLimitError
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.llm.gateway import (
    ModelCallFailedError,
    ModelGateway,
    ModelOutputInvalidError,
    create_adapter,
)
from app.llm.litellm_adapter import LiteLLMAdapter, ModelConfigurationError
from app.llm.mock import MockModelAdapter
from app.llm.types import CallContext, ChatMessage, ModelRequest
from app.models import LlmCallStatus
from app.services.usage import CallRecord
from tests.conftest import make_settings

MODEL = "anthropic/claude-sonnet-5-5"
# Not a real key; LiteLLM never sends it because every call here uses mock_response.
FAKE_KEY = "unit-test-model-key-0000"
CONTEXT = CallContext(workspace_id=uuid.uuid4(), workflow_id=uuid.uuid4())


class Plan(BaseModel):
    title: str
    steps: list[str]


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "model_provider": "litellm",
        "model_name": MODEL,
        "model_api_key": FAKE_KEY,
        "model_timeout_seconds": 30,
    }
    return make_settings(**{**values, **overrides})


class Completion:
    """LiteLLM's real `completion`, offline, recording the arguments of each call."""

    def __init__(self, reply: str | Exception) -> None:
        self.calls: list[dict[str, Any]] = []
        self._reply = reply

    def __call__(self, **arguments: Any) -> Any:
        self.calls.append(arguments)
        return litellm.completion(**arguments, mock_response=self._reply)


class Recorder:
    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    def __call__(self, record: CallRecord) -> uuid.UUID:
        self.records.append(record)
        return uuid.uuid4()


def _gateway(reply: str | Exception, **overrides: Any) -> tuple[ModelGateway, Completion, Recorder]:
    settings = _settings(**overrides)
    completion, recorder = Completion(reply), Recorder()
    return (
        ModelGateway(settings, LiteLLMAdapter(settings, completion), recorder),
        completion,
        recorder,
    )


def _request() -> ModelRequest:
    return ModelRequest(
        purpose="plan_generation",
        messages=[ChatMessage(role="user", content="Plan the confidential launch")],
        temperature=0.2,
        max_output_tokens=500,
    )


def test_litellm_is_offline_and_quiet_once_loaded() -> None:
    assert os.environ["LITELLM_OFFLINE_MODE"] == "true"
    assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "true"
    assert litellm.turn_off_message_logging is True and litellm.suppress_debug_info is True
    assert litellm.disable_hf_tokenizer_download is True
    assert litellm.callbacks == [] and litellm.success_callback == []
    assert litellm.failure_callback == []
    for name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy"):
        logger = logging.getLogger(name)
        assert logger.handlers == [] and logger.level == logging.WARNING and logger.propagate


def test_a_structured_call_records_provider_usage_and_no_cost() -> None:
    gateway, completion, recorder = _gateway('{"title": "Launch", "steps": ["a", "b"]}')

    response = gateway.complete(CONTEXT, _request(), Plan)

    assert response.parsed == Plan(title="Launch", steps=["a", "b"])
    assert (response.provider, response.model) == ("anthropic", MODEL)
    (record,) = recorder.records
    assert record.status == LlmCallStatus.SUCCEEDED and record.error_code is None
    assert (record.provider, record.model, record.purpose) == (
        "anthropic",
        MODEL,
        "plan_generation",
    )
    assert record.usage is not None
    assert (record.usage.input_tokens, record.usage.output_tokens) == (10, 20)
    assert record.usage.other["total_tokens"] == 30
    assert record.usage.other["reported_via"] == "litellm"
    # LiteLLM's own cost estimate is never taken: costs stay NULL until Phase 7 pricing.
    assert not any("cost" in key for key in record.usage.other)
    assert record.provider_request_id and record.provider_request_id == response.provider_request_id

    (arguments,) = completion.calls
    assert arguments["api_key"] == FAKE_KEY and arguments["model"] == MODEL
    assert (arguments["max_retries"], arguments["num_retries"]) == (0, 0)
    assert arguments["drop_params"] is True and arguments["timeout"] == 30
    assert (arguments["temperature"], arguments["max_tokens"]) == (0.2, 500)
    schema = arguments["response_format"]["json_schema"]
    assert schema["name"] == "Plan" and schema["strict"] is False
    assert schema["schema"] == Plan.model_json_schema()


def test_models_without_schema_support_get_no_response_format() -> None:
    gateway, completion, _ = _gateway(
        '{"title": "t", "steps": []}',
        model_name="ollama/llama3",
        model_api_key=None,
        model_api_base="http://127.0.0.1:11434",
    )
    assert gateway.complete(CONTEXT, _request(), Plan).parsed is not None
    (arguments,) = completion.calls
    assert "response_format" not in arguments and "api_key" not in arguments
    assert arguments["api_base"] == "http://127.0.0.1:11434"


def test_invalid_output_is_recorded_with_its_usage() -> None:
    gateway, _, recorder = _gateway("Sure! Here is a plan: step one...")
    with pytest.raises(ModelOutputInvalidError):
        gateway.complete(CONTEXT, _request(), Plan)
    (record,) = recorder.records
    assert record.status == LlmCallStatus.INVALID_OUTPUT
    assert record.usage is not None and record.usage.input_tokens == 10


def test_a_provider_error_is_one_failed_call_without_retries_or_leaked_text() -> None:
    error = RateLimitError(
        "slow down; key unit-test-model-key-0000", llm_provider="anthropic", model=MODEL
    )
    gateway, completion, recorder = _gateway(error)

    with pytest.raises(ModelCallFailedError) as raised:
        gateway.complete(CONTEXT, _request())

    assert len(completion.calls) == 1
    assert FAKE_KEY not in str(raised.value)
    (record,) = recorder.records
    assert record.status == LlmCallStatus.FAILED and record.error_code == "MODEL_CALL_FAILED"
    assert record.usage is None and record.provider == "anthropic"


def test_create_adapter_keeps_the_mock_by_default() -> None:
    assert isinstance(create_adapter(make_settings()), MockModelAdapter)
    assert isinstance(create_adapter(_settings()), LiteLLMAdapter)


def test_an_unroutable_model_name_is_refused_at_startup() -> None:
    with pytest.raises(ModelConfigurationError):
        LiteLLMAdapter(_settings(model_name="not-a-known-model"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"model_api_key": None},  # neither a key nor an endpoint
        {"model_api_base": "file:///etc/passwd"},
        {"model_api_base": "http://host/path with space"},
        {"model_timeout_seconds": 0},
        {"model_provider": "openai"},  # providers are chosen through LiteLLM, not here
    ],
)
def test_invalid_model_settings_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _settings(**overrides)


def test_the_model_key_is_a_secret() -> None:
    settings = _settings()
    assert FAKE_KEY in settings.secret_values()
    assert FAKE_KEY not in repr(settings)
    with pytest.raises(ValidationError, match="MODEL_API_KEY must be set to a real secret"):
        _settings(
            app_env="production",
            cors_allowed_origins=["https://app.example.com"],
            model_api_key="change-me",
        )
