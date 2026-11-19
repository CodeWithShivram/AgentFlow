"""Analytics ranges and totals (Step 7.3, ADR-0041)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models import CostBasis
from app.services.analytics import AnalyticsRangeInvalidError, _Totals, resolve_range

NOW = datetime(2026, 11, 19, 12, tzinfo=UTC)


def test_the_range_defaults_to_the_last_thirty_days() -> None:
    window = resolve_range(None, NOW)
    assert (window.start, window.end) == (NOW - timedelta(days=30), NOW)
    assert resolve_range(NOW - timedelta(days=366), NOW).start == NOW - timedelta(days=366)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (NOW, NOW),  # empty
        (NOW, NOW - timedelta(days=1)),  # backwards
        (NOW - timedelta(days=367), NOW),  # over a year
        (datetime(2026, 11, 1), NOW),  # no time zone
    ],
)
def test_invalid_ranges_are_refused(start: datetime, end: datetime) -> None:
    with pytest.raises(AnalyticsRangeInvalidError):
        resolve_range(start, end)


def test_totals_keep_currencies_and_bases_apart() -> None:
    totals = _Totals()
    for currency, basis, cost in (
        ("USD", CostBasis.ACTUAL, Decimal("0.5")),
        ("USD", CostBasis.ESTIMATED, Decimal("0.25")),
        ("USD", CostBasis.MOCK, Decimal("9")),
        ("EUR", CostBasis.ACTUAL, Decimal("1")),
        (None, None, None),  # unpriced
    ):
        totals.add(
            calls=2,
            input_tokens=10,
            output_tokens=5,
            cached_input_tokens=1,
            currency=currency,
            basis=basis,
            cost=cost,
        )
    model = totals.model()
    assert (model.calls, model.unpriced_calls, model.input_tokens) == (10, 2, 50)
    assert [(c.currency, c.actual, c.estimated, c.mock) for c in model.costs] == [
        ("EUR", Decimal("1"), Decimal(0), Decimal(0)),
        ("USD", Decimal("0.5"), Decimal("0.25"), Decimal("9")),
    ]
    merged = _Totals()
    merged.merge(totals)
    merged.merge(totals)
    assert merged.model().calls == 20 and merged.model().costs[1].actual == Decimal("1.0")
