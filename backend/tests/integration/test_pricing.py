"""Pricing snapshots and the usage tracker (Step 7.1, ADR-0039) against real PostgreSQL."""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from app.db.sync_session import session_scope
from app.llm import pricing
from app.llm.types import CallContext, ProviderUsage
from app.models import LlmCallStatus, LlmUsage, ModelPrice, PricingVersion, UsageSource
from app.services.usage import CallRecord, record_model_call
from tests.integration.api_helpers import API, ClientFactory, new_email, register

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]


def _file(tmp_path: Path, version: str, *, price: str = "1.5", **changes: Any) -> Path:
    content: dict[str, Any] = {
        "version": version,
        "currency": "USD",
        "effective_from": "2026-01-01T00:00:00Z",
        "source": "test",
        "providers": {
            "mock": {
                "reports_usage": True,
                "models": {
                    "mock-1": {"input_per_million": "0", "output_per_million": "0", "mock": True}
                },
            },
            "acme": {
                "reports_usage": True,
                "models": {
                    "acme-large": {
                        "input_per_million": price,
                        "output_per_million": "6",
                        "cached_input_per_million": "0.75",
                    }
                },
            },
            "ollama": {"reports_usage": False, "models": {}},
        },
        **changes,
    }
    path = tmp_path / f"{version}.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def _load(admin: Factory, path: Path) -> pricing.LoadReport:
    with session_scope(admin) as s:
        report = pricing.load(s, path)
        s.commit()
        return report


def _record(
    worker: Factory, admin: Factory, provider: str, model: str, usage: ProviderUsage | None
) -> LlmUsage:
    workspace_id = _workspace(admin)
    usage_id = record_model_call(
        worker,
        CallRecord(
            context=CallContext(workspace_id=workspace_id),
            purpose="test",
            provider=provider,
            model=model,
            status=LlmCallStatus.SUCCEEDED,
            usage=usage,
            provider_request_id=None,
            error_code=None,
            latency_ms=1,
        ),
    )
    with session_scope(admin) as s:
        return s.get_one(LlmUsage, usage_id)


def _workspace(admin: Factory) -> uuid.UUID:
    with session_scope(admin) as s:
        user_id = s.execute(
            text("INSERT INTO users (email, password_hash) VALUES (:e, 'h') RETURNING id"),
            {"e": f"{uuid.uuid4().hex[:8]}@example.com"},
        ).scalar_one()
        workspace_id: uuid.UUID = s.execute(
            text("INSERT INTO workspaces (owner_id, name) VALUES (:u, 'w') RETURNING id"),
            {"u": user_id},
        ).scalar_one()
        s.commit()
        return workspace_id


def _price(admin: Factory, version: str, model: str) -> ModelPrice:
    with session_scope(admin) as s:
        return s.execute(
            select(ModelPrice)
            .join(PricingVersion, PricingVersion.id == ModelPrice.pricing_version_id)
            .where(PricingVersion.version == version, ModelPrice.model == model)
        ).scalar_one()


TOKENS = ProviderUsage(input_tokens=10, output_tokens=5)


def test_a_price_list_loads_once_and_a_changed_one_needs_a_new_version(
    admin_factory: Factory, tmp_path: Path
) -> None:
    first = _file(tmp_path, "t-1")
    assert _load(admin_factory, first) == pricing.LoadReport("t-1", "created", 2)
    assert _load(admin_factory, first).outcome == "unchanged"
    (tmp_path / "changed").mkdir()
    changed = _file(tmp_path / "changed", "t-1", price="2")
    with pytest.raises(pricing.PricingVersionConflictError):
        _load(admin_factory, changed)
    assert _price(admin_factory, "t-1", "acme-large").input_price_per_million == 1.5

    assert _load(admin_factory, _file(tmp_path, "t-2", price="2")).outcome == "created"
    with session_scope(admin_factory) as s:
        active = pricing.active_version(s, datetime.now(UTC))
        assert active is not None and active.version == "t-2"
        # Before a version takes effect, the earlier one stays in force.
        assert pricing.active_version(s, datetime(2025, 1, 1, tzinfo=UTC)) is None


def test_price_lists_are_immutable(admin_factory: Factory, tmp_path: Path) -> None:
    _load(admin_factory, _file(tmp_path, "t-1"))
    for statement in (
        "UPDATE model_prices SET input_price_per_million = 0",
        "DELETE FROM model_prices",
        "UPDATE pricing_versions SET currency = 'EUR'",
    ):
        with session_scope(admin_factory) as s, pytest.raises(DBAPIError, match="immutable"):
            s.execute(text(statement))


def test_calls_are_linked_to_the_price_in_force_with_the_source_of_their_counts(
    admin_factory: Factory, worker_factory: Factory, tmp_path: Path
) -> None:
    # Before any price list: no price, and counts cannot be trusted as reported.
    early = _record(worker_factory, admin_factory, "acme", "acme-large", TOKENS)
    assert early.model_price_id is None and early.usage_source == UsageSource.ESTIMATED

    _load(admin_factory, _file(tmp_path, "t-1"))
    priced = _record(worker_factory, admin_factory, "acme", "acme-large", TOKENS)
    assert priced.model_price_id == _price(admin_factory, "t-1", "acme-large").id
    assert priced.usage_source == UsageSource.REPORTED
    mock = _record(worker_factory, admin_factory, "mock", "mock-1", TOKENS)
    assert _price(admin_factory, "t-1", "mock-1").is_mock and mock.model_price_id is not None
    # OD-21: a provider that does not report usage, or one the list does not know.
    for provider in ("ollama", "unlisted"):
        estimated = _record(worker_factory, admin_factory, provider, "some-model", TOKENS)
        assert estimated.usage_source == UsageSource.ESTIMATED and estimated.model_price_id is None
    silent = _record(worker_factory, admin_factory, "acme", "acme-large", None)
    assert silent.usage_source == UsageSource.NOT_REPORTED
    # Step 7.2 (ADR-0040): costed from that price (10 in at 1.5 and 5 out at 6, per million).
    assert (priced.total_cost, priced.currency, priced.pricing_version) == (
        Decimal("0.000045"),
        "USD",
        "t-1",
    )

    # A newer list applies to later calls only; earlier calls keep the price they were made at.
    _load(admin_factory, _file(tmp_path, "t-2", price="2"))
    later = _record(worker_factory, admin_factory, "acme", "acme-large", TOKENS)
    assert later.model_price_id == _price(admin_factory, "t-2", "acme-large").id
    with session_scope(admin_factory) as s:
        assert (
            s.get_one(LlmUsage, priced.id).model_price_id
            == _price(admin_factory, "t-1", "acme-large").id
        )


def test_applications_can_only_read_prices(
    admin_factory: Factory, worker_factory: Factory, tmp_path: Path
) -> None:
    _load(admin_factory, _file(tmp_path, "t-1"))
    with session_scope(worker_factory) as s:
        assert s.execute(select(ModelPrice)).scalars().all()
        with pytest.raises(ProgrammingError, match="permission denied"):
            s.execute(text("DELETE FROM pricing_versions"))


def test_signed_in_users_read_the_price_list_in_force(
    new_client: ClientFactory, admin_factory: Factory, tmp_path: Path
) -> None:
    anonymous = new_client()
    assert anonymous.get(f"{API}/pricing").status_code == 401
    client: TestClient = new_client()
    assert register(client, new_email()).status_code == 201
    missing = client.get(f"{API}/pricing")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "PRICING_NOT_LOADED"

    _load(admin_factory, _file(tmp_path, "t-1"))
    body = client.get(f"{API}/pricing").json()
    assert (body["version"], body["currency"]) == ("t-1", "USD")
    providers = {p["provider"]: p for p in body["providers"]}
    assert providers["ollama"] == {"provider": "ollama", "reports_usage": False, "models": []}
    (large,) = providers["acme"]["models"]
    assert large["model"] == "acme-large" and large["input_price_per_million"] == "1.50000000"
    assert providers["mock"]["models"][0]["is_mock"] is True


def test_the_repository_price_list_loads(admin_factory: Factory) -> None:
    report = _load(admin_factory, pricing.PRICING_FILE)
    assert report.outcome == "created"
    price = _price(admin_factory, report.version, "mock-1")
    assert price.is_mock and price.input_price_per_million == 0 == price.output_price_per_million
    with session_scope(admin_factory) as s:
        version = s.execute(
            select(PricingVersion).where(PricingVersion.version == report.version)
        ).scalar_one()
    assert version.effective_from <= datetime.now(UTC)
