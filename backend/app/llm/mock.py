"""Deterministic mock provider for tests, development and E2E runs. Never calls a network.

- Tests script exact replies per purpose (`script`); replies are consumed in order and may be
  exceptions to simulate provider failures.
- Without a script, a purpose registered in DEFAULT_RESPONSES gets its canned reply, which
  must be valid for the schema that purpose requests (later steps register their purposes).
- An unscripted purpose that requests structured output fails loudly; plain-text calls get a
  deterministic placeholder.

Usage is reported like a provider would: whitespace-separated word counts, and a request ID
derived from the request and reply, so identical calls produce identical records.
"""

import hashlib
import json
from collections import defaultdict, deque
from collections.abc import Mapping

from pydantic import BaseModel

from app.llm.types import AdapterResult, ModelRequest, ProviderUsage

# purpose -> canned reply (JSON text for structured purposes).
DEFAULT_RESPONSES: dict[str, str] = {}


class MockProviderError(Exception):
    """A scripted provider failure."""


class MockNotScriptedError(Exception):
    """A structured call for a purpose with neither a script nor a default reply."""


class MockModelAdapter:
    def __init__(self, defaults: Mapping[str, str] | None = None) -> None:
        self._defaults = dict(DEFAULT_RESPONSES if defaults is None else defaults)
        self._scripts: dict[str, deque[str | Exception]] = defaultdict(deque)
        self.requests: list[ModelRequest] = []

    @property
    def provider(self) -> str:
        return "mock"

    def script(self, purpose: str, *replies: str | Exception) -> None:
        """Queues replies for `purpose`; each call consumes one."""
        self._scripts[purpose].extend(replies)

    def complete(
        self, request: ModelRequest, *, model: str, response_format: type[BaseModel] | None
    ) -> AdapterResult:
        self.requests.append(request)
        prompt = json.dumps([m.model_dump() for m in request.messages], sort_keys=True)
        queue = self._scripts.get(request.purpose)
        if queue:
            reply = queue.popleft()
            if isinstance(reply, Exception):
                raise reply
            text = reply
        elif request.purpose in self._defaults:
            text = self._defaults[request.purpose]
        elif response_format is None:
            digest = hashlib.sha256(prompt.encode()).hexdigest()[:12]
            text = f"Mock response for {request.purpose} ({digest})."
        else:
            raise MockNotScriptedError(f"no mock reply for purpose {request.purpose!r}")
        request_id = hashlib.sha256(f"{model}\n{prompt}\n{text}".encode()).hexdigest()[:24]
        return AdapterResult(
            text=text,
            usage=ProviderUsage(
                input_tokens=sum(len(m.content.split()) for m in request.messages),
                output_tokens=len(text.split()),
                cached_input_tokens=0,
            ),
            provider_request_id=f"mock-{request_id}",
        )
