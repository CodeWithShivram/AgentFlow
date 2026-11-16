"""The curated price list and usage sources (Step 7.1, ADR-0039)."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.llm import pricing
from app.llm.types import ProviderUsage
from app.models import PricingVersion, UsageSource
from app.services.usage import usage_source


def _content(**changes: Any) -> dict[str, Any]:
    return {
        "version": "v1",
        "currency": "USD",
        "effective_from": "2026-01-01T00:00:00Z",
        "source": "test",
        "providers": {
            "acme": {
                "reports_usage": True,
                "models": {"m": {"input_per_million": "1", "output_per_million": "2"}},
            }
        },
        **changes,
    }


def test_the_repository_price_list_is_valid_and_prices_only_the_mock() -> None:
    parsed, digest = pricing.read_file()
    assert len(digest) == 64
    priced = {
        (provider, model): price
        for provider, entry in parsed.providers.items()
        for model, price in entry.models.items()
    }
    assert set(priced) == {("mock", "mock-1")}
    assert priced[("mock", "mock-1")].mock and priced[("mock", "mock-1")].input_per_million == 0
    assert parsed.providers["ollama"].reports_usage is False


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": "usd"},
        {"effective_from": "2026-01-01T00:00:00"},  # needs a time zone
        {
            "providers": {
                "acme": {
                    "reports_usage": True,
                    "models": {"m": {"input_per_million": "-1", "output_per_million": "0"}},
                }
            }
        },
        {"providers": {"acme": {"models": {}}}},  # reports_usage is required
        {"extra": True},
    ],
)
def test_malformed_price_lists_are_refused(tmp_path: Path, changes: dict[str, Any]) -> None:
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(_content(**changes)), encoding="utf-8")
    with pytest.raises(ValidationError):
        pricing.read_file(path)


def test_the_content_hash_ignores_formatting(tmp_path: Path) -> None:
    compact, spaced = tmp_path / "a.json", tmp_path / "b.json"
    compact.write_text(json.dumps(_content()), encoding="utf-8")
    spaced.write_text(json.dumps(_content(), indent=4), encoding="utf-8")
    assert pricing.read_file(compact)[1] == pricing.read_file(spaced)[1]


def test_usage_is_reported_only_by_providers_that_report_it() -> None:
    counts = ProviderUsage(input_tokens=3, output_tokens=1)
    assert usage_source(counts, provider_reports_usage=True) == UsageSource.REPORTED
    assert usage_source(counts, provider_reports_usage=False) == UsageSource.ESTIMATED
    assert usage_source(None, provider_reports_usage=True) == UsageSource.NOT_REPORTED
    empty = ProviderUsage(input_tokens=None, output_tokens=None)
    assert usage_source(empty, provider_reports_usage=True) == UsageSource.NOT_REPORTED
    version = PricingVersion(
        providers={"acme": {"reports_usage": True}, "ollama": {"reports_usage": False}}
    )
    assert pricing.reports_usage(version, "acme") is True
    assert pricing.reports_usage(version, "ollama") is False
    assert pricing.reports_usage(version, "unlisted") is False
    assert pricing.reports_usage(None, "acme") is False
