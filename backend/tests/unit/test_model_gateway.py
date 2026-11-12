"""Model gateway and mock adapter (ADR-0013), without a database: usage records are captured."""

import logging
import uuid
from typing import Any

import pytest
from pydantic import BaseModel

from app.llm.gateway import (
    ModelCallFailedError,
    ModelGateway,
    ModelOutputInvalidError,
    UnknownModelProfileError,
    create_adapter,
)
from app.llm.mock import MockModelAdapter, MockNotScriptedError, MockProviderError
from app.llm.types import CallContext, ChatMessage, ModelRequest
from app.models import LlmCallStatus
from app.services.usage import CallRecord
from tests.conftest import make_settings

CONTEXT = CallContext(workspace_id=uuid.uuid4(), workflow_id=uuid.uuid4())
SECRET_PROMPT = "summarise the confidential merger memo"


class Summary(BaseModel):
    title: str
    points: list[str]


def _request(purpose: str = "summary", **overrides: Any) -> ModelRequest:
    values: dict[str, Any] = {
        "purpose": purpose,
        "messages": [ChatMessage(role="user", content=SECRET_PROMPT)],
    }
    values.update(overrides)
    return ModelRequest(**values)


class Recorder:
    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    def __call__(self, record: CallRecord) -> uuid.UUID:
        self.records.append(record)
        return uuid.uuid4()


def _gateway(adapter: MockModelAdapter, recorder: Recorder) -> ModelGateway:
    ticks = iter([10.0, 10.25])  # start, end: 250 ms
    return ModelGateway(make_settings(), adapter, recorder, clock=lambda: next(ticks))


# --- mock adapter ----------------------------------------------------------------------------


def test_scripted_replies_are_consumed_in_order_and_may_fail() -> None:
    adapter = MockModelAdapter(defaults={})
    adapter.script("summary", "first", MockProviderError("rate limited"), "third")

    assert adapter.complete(_request(), model="m", response_format=None).text == "first"
    with pytest.raises(MockProviderError):
        adapter.complete(_request(), model="m", response_format=None)
    assert adapter.complete(_request(), model="m", response_format=None).text == "third"


def test_defaults_answer_unscripted_purposes_and_unknown_structured_calls_fail() -> None:
    adapter = MockModelAdapter(defaults={"summary": '{"title": "T", "points": []}'})

    assert adapter.complete(_request(), model="m", response_format=Summary).text.startswith("{")
    with pytest.raises(MockNotScriptedError):
        adapter.complete(_request("other"), model="m", response_format=Summary)


def test_mock_is_deterministic_and_reports_usage_like_a_provider() -> None:
    first = MockModelAdapter(defaults={}).complete(_request(), model="m", response_format=None)
    second = MockModelAdapter(defaults={}).complete(_request(), model="m", response_format=None)

    assert first == second
    assert first.text.startswith("Mock response for summary")
    assert first.usage.input_tokens == len(SECRET_PROMPT.split())
    assert first.usage.output_tokens == len(first.text.split())
    assert first.provider_request_id is not None and first.provider_request_id.startswith("mock-")


# --- gateway ---------------------------------------------------------------------------------


def test_structured_success_is_parsed_and_recorded_with_provider_usage() -> None:
    adapter, recorder = MockModelAdapter(defaults={}), Recorder()
    adapter.script("summary", '{"title": "Merger", "points": ["a", "b"]}')

    response = _gateway(adapter, recorder).complete(CONTEXT, _request(), Summary)

    assert response.parsed == Summary(title="Merger", points=["a", "b"])
    assert (response.provider, response.model) == ("mock", "mock-1")
    (record,) = recorder.records
    assert record.status is LlmCallStatus.SUCCEEDED
    assert record.context == CONTEXT
    assert (record.purpose, record.provider, record.model) == ("summary", "mock", "mock-1")
    assert record.usage == response.usage and record.usage is not None
    assert record.error_code is None
    assert record.latency_ms == 250


def test_output_that_fails_the_schema_is_rejected_and_still_recorded() -> None:
    adapter, recorder = MockModelAdapter(defaults={}), Recorder()
    adapter.script("summary", '{"title": "missing points", "leak": "MODEL TEXT"}')

    with pytest.raises(ModelOutputInvalidError) as exc_info:
        _gateway(adapter, recorder).complete(CONTEXT, _request(), Summary)

    assert "MODEL TEXT" not in str(exc_info.value)
    (record,) = recorder.records
    assert record.status is LlmCallStatus.INVALID_OUTPUT
    assert record.error_code == "MODEL_OUTPUT_INVALID"
    # The provider did the work, so its reported usage is kept.
    assert record.usage is not None and record.usage.output_tokens is not None


def test_provider_failures_are_recorded_without_usage() -> None:
    adapter, recorder = MockModelAdapter(defaults={}), Recorder()
    adapter.script("summary", MockProviderError("upstream 500 with secret details"))

    with pytest.raises(ModelCallFailedError) as exc_info:
        _gateway(adapter, recorder).complete(CONTEXT, _request(), Summary)

    assert "secret details" not in str(exc_info.value)
    (record,) = recorder.records
    assert record.status is LlmCallStatus.FAILED
    assert record.error_code == "MODEL_CALL_FAILED"
    assert record.usage is None and record.provider_request_id is None


def test_unknown_profiles_are_rejected_before_any_call() -> None:
    adapter, recorder = MockModelAdapter(defaults={}), Recorder()

    with pytest.raises(UnknownModelProfileError):
        _gateway(adapter, recorder).complete(CONTEXT, _request(profile="premium"))

    assert adapter.requests == []
    assert recorder.records == []


def test_logs_carry_metadata_but_never_prompts_or_outputs(caplog: pytest.LogCaptureFixture) -> None:
    adapter, recorder = MockModelAdapter(defaults={}), Recorder()
    adapter.script("summary", '{"title": "OUTPUT-MARKER", "points": []}')

    with caplog.at_level(logging.INFO, logger="agentflow.llm"):
        _gateway(adapter, recorder).complete(CONTEXT, _request(), Summary)

    (entry,) = [r for r in caplog.records if r.name == "agentflow.llm"]
    assert entry.__dict__["purpose"] == "summary"
    assert entry.__dict__["status"] == "SUCCEEDED"
    rendered = str(entry.__dict__)
    assert SECRET_PROMPT not in rendered
    assert "OUTPUT-MARKER" not in rendered


def test_the_configured_provider_and_model_are_used() -> None:
    settings = make_settings(model_name="mock-large")

    assert create_adapter(settings).provider == "mock"
    gateway = ModelGateway(settings, create_adapter(settings), Recorder())
    assert gateway.resolve_model("default") == "mock-large"


@pytest.mark.parametrize(
    "overrides",
    [
        {"purpose": "Bad Purpose"},
        {"messages": []},
        {"temperature": 3},
        {"max_output_tokens": 0},
    ],
)
def test_requests_are_validated(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _request(**overrides)
