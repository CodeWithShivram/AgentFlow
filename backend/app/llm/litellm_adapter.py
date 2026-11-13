"""The LiteLLM provider adapter (ADR-0020): real models behind the model gateway.

Imported only when MODEL_PROVIDER=litellm, by worker processes. The gateway still owns usage
records, output validation and logging; this adapter only translates a request and reports what
the provider returned.

Hardening:
- LiteLLM is put in offline mode before it is imported, so it never fetches price maps,
  tokenizers or anything else on its own. Its cost estimates are never read (costs stay NULL
  until Phase 7 pricing).
- No logging integrations, message logging or debug output; LiteLLM's loggers go through the
  application's redacting JSON handler at WARNING.
- The API key comes only from Settings and is passed on each call. LiteLLM's own retries are
  off: each billed request is one gateway call and one usage record.
"""

import logging
import os
import warnings
from collections.abc import Callable
from typing import Any

# LiteLLM reads these when it is imported; this module is the only place they are set.
os.environ["LITELLM_OFFLINE_MODE"] = "true"
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "true"
# Pydantic warns about `ReadOnly` items in LiteLLM's internal TypedDicts on first use. Only
# that exact message is silenced; it says nothing about AgentFlow data.
warnings.filterwarnings(
    "ignore",
    message=r"Items? .* on TypedDict class '\w+' (is|are) using the `ReadOnly` qualifier",
    category=UserWarning,
)

import litellm  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.llm.types import AdapterResult, ModelRequest, ProviderUsage  # noqa: E402

_LITELLM_LOGGERS = ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy")
# Usage fields recorded as columns; everything else the provider reported goes to `other`.
_COLUMN_FIELDS = {"prompt_tokens", "completion_tokens"}


def _harden_litellm() -> None:
    litellm.turn_off_message_logging = True
    litellm.suppress_debug_info = True
    litellm.disable_hf_tokenizer_download = True
    litellm.callbacks = []
    litellm.success_callback = []
    litellm.failure_callback = []
    for name in _LITELLM_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.setLevel(logging.WARNING)
        logger.propagate = True


_harden_litellm()


class ModelConfigurationError(ValueError):
    """MODEL_NAME is not a model LiteLLM can route."""


def _usage(response: Any) -> ProviderUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return ProviderUsage(input_tokens=None, output_tokens=None)
    reported: dict[str, Any] = usage.model_dump(exclude_none=True)
    details = reported.get("prompt_tokens_details") or {}
    other = {k: v for k, v in reported.items() if k not in _COLUMN_FIELDS}
    # LiteLLM normalises provider usage; for some providers it fills missing counts with its
    # own tokenizer without marking them (ADR-0020), so the source is recorded with the counts.
    other["reported_via"] = "litellm"
    return ProviderUsage(
        input_tokens=reported.get("prompt_tokens"),
        output_tokens=reported.get("completion_tokens"),
        cached_input_tokens=details.get("cached_tokens"),
        other=other,
    )


class LiteLLMAdapter:
    def __init__(
        self, settings: Settings, completion: Callable[..., Any] = litellm.completion
    ) -> None:
        """`completion` is LiteLLM's; tests pass it with a `mock_response` to stay offline."""
        self._api_key = settings.model_api_key
        self._api_base = settings.model_api_base
        self._timeout = settings.model_timeout_seconds
        self._completion = completion
        try:
            _, provider, _, _ = litellm.get_llm_provider(
                settings.model_name, api_base=self._api_base
            )
        except Exception as exc:  # LiteLLM raises its own BadRequestError
            raise ModelConfigurationError(
                "MODEL_NAME is not a model LiteLLM can route (use e.g. 'anthropic/<model>')"
            ) from exc
        self._provider = str(provider)

    @property
    def provider(self) -> str:
        return self._provider

    def complete(
        self, request: ModelRequest, *, model: str, response_format: type[BaseModel] | None
    ) -> AdapterResult:
        arguments: dict[str, Any] = {
            "model": model,
            "messages": [message.model_dump() for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            "timeout": self._timeout,
            "max_retries": 0,
            "num_retries": 0,
            # Models differ in the settings they accept (some allow only temperature 1); an
            # unsupported setting is dropped instead of failing the call.
            "drop_params": True,
        }
        if self._api_key is not None:
            arguments["api_key"] = self._api_key.get_secret_value()
        if self._api_base is not None:
            arguments["api_base"] = self._api_base
        if response_format is not None and litellm.supports_response_schema(model=model):
            # Not strict: providers differ in the JSON Schema they accept. The gateway validates
            # the reply against the Pydantic model either way.
            arguments["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_format.__name__,
                    "schema": response_format.model_json_schema(),
                    "strict": False,
                },
            }
        response = self._completion(**arguments)
        content = response.choices[0].message.content
        return AdapterResult(
            text=content if isinstance(content, str) else "",
            usage=_usage(response),
            provider_request_id=getattr(response, "id", None),
        )
