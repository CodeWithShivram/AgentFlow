"""Cost arithmetic and cost bases (Step 7.2, ADR-0040)."""

from decimal import Decimal
from typing import Any

from app.llm.costing import CallCost, compute_cost, cost_basis
from app.models import CostBasis, ModelPrice, UsageSource


def _price(**changes: Any) -> ModelPrice:
    values: dict[str, Any] = {
        "provider": "acme",
        "model": "acme-large",
        "input_price_per_million": Decimal("1.5"),
        "output_price_per_million": Decimal("6"),
        "cached_input_price_per_million": Decimal("0.75"),
        "is_mock": False,
    }
    return ModelPrice(**{**values, **changes})


def test_input_and_output_are_priced_per_million_tokens() -> None:
    assert compute_cost(1000, 500, None, _price()) == CallCost(
        Decimal("0.0015"), Decimal("0.003"), Decimal("0.0045")
    )
    assert compute_cost(0, 0, 0, _price()) == CallCost(Decimal(0), Decimal(0), Decimal(0))


def test_cached_input_uses_the_cached_price_or_else_the_input_price() -> None:
    # 600 billable at 1.5 plus 400 cached at 0.75.
    assert compute_cost(1000, 0, 400, _price()).input_cost == Decimal("0.0012")  # type: ignore[union-attr]
    no_cached_price = _price(cached_input_price_per_million=None)
    assert compute_cost(1000, 0, 400, no_cached_price).input_cost == Decimal("0.0015")  # type: ignore[union-attr]
    # More cached tokens than input tokens cannot make the input cheaper than all-cached.
    assert compute_cost(100, 0, 500, _price()).input_cost == Decimal("0.000075")  # type: ignore[union-attr]


def test_amounts_are_rounded_half_up_to_eight_places() -> None:
    tiny = _price(
        input_price_per_million=Decimal("0.005"), output_price_per_million=Decimal("0.015")
    )
    # 1 token at 0.005/M = 0.000000005 -> 0.00000001; 1 at 0.015/M = 0.000000015 -> 0.00000002.
    assert compute_cost(1, 1, None, tiny) == CallCost(
        Decimal("0.00000001"), Decimal("0.00000002"), Decimal("0.00000003")
    )


def test_a_call_without_both_counts_is_not_priced() -> None:
    assert compute_cost(None, 10, None, _price()) is None
    assert compute_cost(10, None, None, _price()) is None


def test_the_basis_never_presents_an_estimate_or_a_mock_as_actual() -> None:
    assert cost_basis(_price(), UsageSource.REPORTED) == CostBasis.ACTUAL
    assert cost_basis(_price(), UsageSource.ESTIMATED) == CostBasis.ESTIMATED
    assert cost_basis(_price(), UsageSource.NOT_REPORTED) is None
    assert cost_basis(_price(is_mock=True), UsageSource.REPORTED) == CostBasis.MOCK
