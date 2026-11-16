"""pricing snapshots

Adds immutable, versioned model prices (`pricing_versions`, `model_prices`; triggers refuse
updates and deletes) loaded from the curated price list, and links each `llm_usage` row to the
price in force (`model_price_id`) with the source of its token counts (`usage_source`: reported,
estimated or not reported; OD-21). Existing usage is backfilled: no counts -> not_reported, the
mock provider -> reported, any other provider -> estimated (Phase 7, Step 7.1, ADR-0039).

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-16 13:37:14.523825+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

IMMUTABLE = ("pricing_versions", "model_prices")

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pricing_versions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("providers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "loaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name=op.f("ck_pricing_versions_currency_iso4217")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pricing_versions")),
        sa.UniqueConstraint("version", name=op.f("uq_pricing_versions_version")),
    )
    op.create_table(
        "model_prices",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("pricing_version_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("input_price_per_million", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("output_price_per_million", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column(
            "cached_input_price_per_million", sa.Numeric(precision=18, scale=8), nullable=True
        ),
        sa.Column("is_mock", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "input_price_per_million >= 0 AND output_price_per_million >= 0 "
            "AND (cached_input_price_per_million IS NULL OR cached_input_price_per_million >= 0)",
            name=op.f("ck_model_prices_prices_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["pricing_version_id"],
            ["pricing_versions.id"],
            name=op.f("fk_model_prices_pricing_version_id_pricing_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_prices")),
        sa.UniqueConstraint(
            "pricing_version_id",
            "provider",
            "model",
            name=op.f("uq_model_prices_pricing_version_id_provider_model"),
        ),
    )
    op.create_index(
        op.f("ix_model_prices_pricing_version_id"),
        "model_prices",
        ["pricing_version_id"],
        unique=False,
    )
    for table in IMMUTABLE:
        op.execute(
            f"""
            CREATE FUNCTION {table}_reject_change() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION '{table} rows are immutable; load a new pricing version'
                    USING ERRCODE = 'restrict_violation';
            END;
            $$
            """
        )
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {table}_reject_change()"
        )
    op.add_column(
        "llm_usage",
        sa.Column(
            "usage_source",
            sa.Enum(
                "reported",
                "estimated",
                "not_reported",
                name="usage_source",
                native_enum=False,
                create_constraint=True,
                length=12,
            ),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE llm_usage SET usage_source = CASE
            WHEN input_tokens IS NULL AND output_tokens IS NULL THEN 'not_reported'
            WHEN provider = 'mock' THEN 'reported'
            ELSE 'estimated'
        END
        """
    )
    op.alter_column("llm_usage", "usage_source", nullable=False)
    op.add_column("llm_usage", sa.Column("model_price_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_llm_usage_model_price_id"), "llm_usage", ["model_price_id"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_llm_usage_model_price_id_model_prices"),
        "llm_usage",
        "model_prices",
        ["model_price_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_llm_usage_model_price_id_model_prices"), "llm_usage", type_="foreignkey"
    )
    op.drop_index(op.f("ix_llm_usage_model_price_id"), table_name="llm_usage")
    op.drop_column("llm_usage", "model_price_id")
    op.drop_column("llm_usage", "usage_source")
    op.drop_index(op.f("ix_model_prices_pricing_version_id"), table_name="model_prices")
    for table in IMMUTABLE:
        op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
        op.execute(f"DROP FUNCTION {table}_reject_change()")
    op.drop_table("model_prices")
    op.drop_table("pricing_versions")
