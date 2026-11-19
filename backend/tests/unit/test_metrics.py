"""Rate and distribution helpers (Step 7.5, ADR-0043)."""

from decimal import Decimal

from app.services.metrics import _distribution, rate


def test_a_rate_keeps_its_parts_and_is_null_without_a_denominator() -> None:
    assert rate(1, 3).model_dump() == {"numerator": 1, "denominator": 3, "value": 0.333333}
    assert rate(0, 5).value == 0.0
    assert rate(0, 0).value is None


def test_a_distribution_without_samples_is_null_not_zero() -> None:
    assert _distribution("ms", (0, None, None, None, None)).model_dump() == {
        "unit": "ms",
        "samples": 0,
        "p50": None,
        "p95": None,
        "max": None,
        "mean": None,
    }
    measured = _distribution("s", (2, 1.5, 1.95, Decimal("2"), Decimal("1.5")))
    assert (measured.samples, measured.p50, measured.p95, measured.max, measured.mean) == (
        2,
        1.5,
        1.95,
        2.0,
        1.5,
    )
