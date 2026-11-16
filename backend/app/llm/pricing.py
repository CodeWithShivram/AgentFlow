"""Loads the curated price list (`pricing.json`) as an immutable pricing snapshot (ADR-0039).

A new `version` label creates a new snapshot; loading an unchanged file again changes nothing,
and a changed file under an existing label is refused. Nothing is fetched from the network.
Run as the administrator after migrations:

    uv run python -m app.llm.pricing load
"""

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ModelPrice, PricingVersion

PRICING_FILE = Path(__file__).with_name("pricing.json")

Price = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=8)]
Name = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class ModelPriceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_per_million: Price
    output_per_million: Price
    cached_input_per_million: Price | None = None
    mock: bool = False


class ProviderEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Whether the provider reports its own token usage; when False, counts are estimates.
    reports_usage: bool
    models: dict[Name, ModelPriceEntry] = {}


class PricingFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    effective_from: AwareDatetime
    source: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    providers: dict[Annotated[str, StringConstraints(min_length=1, max_length=50)], ProviderEntry]


class PricingVersionConflictError(ValueError):
    """The file changed but kept a version label that is already loaded."""


@dataclass(frozen=True)
class LoadReport:
    version: str
    outcome: Literal["created", "unchanged"]
    models: int


def read_file(path: Path = PRICING_FILE) -> tuple[PricingFile, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    parsed = PricingFile.model_validate(raw)
    canonical = json.dumps(parsed.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return parsed, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load(session: Session, path: Path = PRICING_FILE) -> LoadReport:
    """Writes the file as a new snapshot version, in the caller's transaction."""
    parsed, digest = read_file(path)
    existing = session.execute(
        select(PricingVersion).where(PricingVersion.version == parsed.version)
    ).scalar_one_or_none()
    models = sum(len(p.models) for p in parsed.providers.values())
    if existing is not None:
        if existing.content_sha256 != digest:
            raise PricingVersionConflictError(
                f"pricing version {parsed.version!r} is already loaded with different content; "
                "give the changed file a new version"
            )
        return LoadReport(parsed.version, "unchanged", models)
    version = PricingVersion(
        version=parsed.version,
        currency=parsed.currency,
        effective_from=parsed.effective_from,
        source=parsed.source,
        providers={
            name: {"reports_usage": entry.reports_usage}
            for name, entry in sorted(parsed.providers.items())
        },
        content_sha256=digest,
    )
    session.add(version)
    session.flush()
    for provider, entry in sorted(parsed.providers.items()):
        for model, price in sorted(entry.models.items()):
            session.add(
                ModelPrice(
                    pricing_version_id=version.id,
                    provider=provider,
                    model=model,
                    input_price_per_million=price.input_per_million,
                    output_price_per_million=price.output_per_million,
                    cached_input_price_per_million=price.cached_input_per_million,
                    is_mock=price.mock,
                )
            )
    session.flush()
    return LoadReport(parsed.version, "created", models)


def active_version(session: Session, at: datetime) -> PricingVersion | None:
    """The snapshot in force at `at`: the latest effective one, the latest loaded on a tie."""
    return session.execute(
        select(PricingVersion)
        .where(PricingVersion.effective_from <= at)
        .order_by(PricingVersion.effective_from.desc(), PricingVersion.loaded_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def reports_usage(version: PricingVersion | None, provider: str) -> bool:
    """Whether `provider` reports its own token usage. Unlisted providers do not, so their
    counts are recorded as estimated until the price list says otherwise (OD-21)."""
    if version is None:
        return False
    entry: dict[str, Any] = version.providers.get(provider) or {}
    return bool(entry.get("reports_usage", False))


def _main() -> int:
    if sys.argv[1:] != ["load"]:
        print("usage: python -m app.llm.pricing load", file=sys.stderr)
        return 2
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session, session.begin():
            report = load(session)
    finally:
        engine.dispose()
    # Command-line output for the operator, not application logging.
    print(f"pricing {report.version}: {report.outcome} ({report.models} model prices)")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
