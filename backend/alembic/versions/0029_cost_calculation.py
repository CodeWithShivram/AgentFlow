"""cost calculation

Adds `llm_usage.cost_basis` (actual, estimated or mock; set with the costs) and the
BUDGET_REACHED pause reason (Phase 7, Step 7.2, ADR-0040). Usage already linked to a price is
costed here with the same rule as `app.llm.costing`: cached input at the cached price, or the
input price without one; amounts rounded half up to 8 places; only calls with both counts
reported or estimated. The downgrade keeps the costs, drops the basis, and turns budget pauses
into a person's pause so the older check holds.

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-18 13:07:43.447123+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PAUSE_REASONS_0025 = ("USER", "DEADLINE_PASSED", "AWAITING_DECISION")


def _pause_reason_check(values: Sequence[str]) -> None:
    op.drop_constraint(op.f("ck_workflows_pause_reason"), "workflows", type_="check")
    op.create_check_constraint(
        op.f("ck_workflows_pause_reason"),
        "workflows",
        f"pause_reason IN ({', '.join(repr(v) for v in values)})",
    )


def upgrade() -> None:
    op.add_column(
        "llm_usage",
        sa.Column(
            "cost_basis",
            sa.Enum(
                "actual",
                "estimated",
                "mock",
                name="cost_basis",
                native_enum=False,
                create_constraint=True,
                length=9,
            ),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE llm_usage AS u
        SET input_cost = ROUND(
                ((u.input_tokens - LEAST(COALESCE(u.cached_input_tokens, 0), u.input_tokens))
                    * p.input_price_per_million
                 + LEAST(COALESCE(u.cached_input_tokens, 0), u.input_tokens)
                    * COALESCE(p.cached_input_price_per_million, p.input_price_per_million))
                / 1000000, 8),
            output_cost = ROUND(u.output_tokens * p.output_price_per_million / 1000000, 8),
            total_cost = ROUND(
                ((u.input_tokens - LEAST(COALESCE(u.cached_input_tokens, 0), u.input_tokens))
                    * p.input_price_per_million
                 + LEAST(COALESCE(u.cached_input_tokens, 0), u.input_tokens)
                    * COALESCE(p.cached_input_price_per_million, p.input_price_per_million))
                / 1000000, 8)
                + ROUND(u.output_tokens * p.output_price_per_million / 1000000, 8),
            currency = v.currency,
            pricing_version = v.version,
            cost_basis = CASE
                WHEN p.is_mock THEN 'mock'
                WHEN u.usage_source = 'reported' THEN 'actual'
                ELSE 'estimated'
            END
        FROM model_prices AS p
        JOIN pricing_versions AS v ON v.id = p.pricing_version_id
        WHERE p.id = u.model_price_id
          AND u.total_cost IS NULL
          AND u.input_tokens IS NOT NULL
          AND u.output_tokens IS NOT NULL
          AND u.usage_source IN ('reported', 'estimated')
        """
    )
    op.create_check_constraint(
        op.f("ck_llm_usage_cost_has_basis"),
        "llm_usage",
        "(cost_basis IS NULL) = (total_cost IS NULL)",
    )
    _pause_reason_check((*PAUSE_REASONS_0025, "BUDGET_REACHED"))


def downgrade() -> None:
    op.execute("UPDATE workflows SET pause_reason = 'USER' WHERE pause_reason = 'BUDGET_REACHED'")
    _pause_reason_check(PAUSE_REASONS_0025)
    op.drop_constraint(op.f("ck_llm_usage_cost_has_basis"), "llm_usage", type_="check")
    op.drop_column("llm_usage", "cost_basis")
