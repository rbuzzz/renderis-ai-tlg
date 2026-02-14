"""support fractional generation prices and balances

Revision ID: 0008_fractional_credits
Revises: 0007_ai_brain_layer
Create Date: 2026-02-14
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_fractional_credits"
down_revision = "0007_ai_brain_layer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "balance_credits",
        existing_type=sa.Integer(),
        type_=sa.Numeric(12, 3),
        existing_nullable=False,
        postgresql_using="balance_credits::numeric(12,3)",
    )
    op.alter_column(
        "credit_ledger",
        "delta_credits",
        existing_type=sa.Integer(),
        type_=sa.Numeric(12, 3),
        existing_nullable=False,
        postgresql_using="delta_credits::numeric(12,3)",
    )
    op.alter_column(
        "prices",
        "price_credits",
        existing_type=sa.Integer(),
        type_=sa.Numeric(12, 3),
        existing_nullable=False,
        postgresql_using="price_credits::numeric(12,3)",
    )
    op.alter_column(
        "generations",
        "total_cost_credits",
        existing_type=sa.Integer(),
        type_=sa.Numeric(12, 3),
        existing_nullable=False,
        postgresql_using="total_cost_credits::numeric(12,3)",
    )
    op.alter_column(
        "generations",
        "final_cost_credits",
        existing_type=sa.Integer(),
        type_=sa.Numeric(12, 3),
        existing_nullable=False,
        postgresql_using="final_cost_credits::numeric(12,3)",
    )


def downgrade() -> None:
    op.alter_column(
        "generations",
        "final_cost_credits",
        existing_type=sa.Numeric(12, 3),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="round(final_cost_credits)::integer",
    )
    op.alter_column(
        "generations",
        "total_cost_credits",
        existing_type=sa.Numeric(12, 3),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="round(total_cost_credits)::integer",
    )
    op.alter_column(
        "prices",
        "price_credits",
        existing_type=sa.Numeric(12, 3),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="round(price_credits)::integer",
    )
    op.alter_column(
        "credit_ledger",
        "delta_credits",
        existing_type=sa.Numeric(12, 3),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="round(delta_credits)::integer",
    )
    op.alter_column(
        "users",
        "balance_credits",
        existing_type=sa.Numeric(12, 3),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="round(balance_credits)::integer",
    )
