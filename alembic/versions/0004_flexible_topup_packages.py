"""add flexible top-up package fields

Revision ID: 0004_flexible_topup_packages
Revises: 0003_support_chats
Create Date: 2026-02-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_flexible_topup_packages"
down_revision = "0003_support_chats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("star_products")}

    if "credits_base" not in columns:
        op.add_column("star_products", sa.Column("credits_base", sa.Integer(), nullable=False, server_default="0"))
    if "credits_bonus" not in columns:
        op.add_column("star_products", sa.Column("credits_bonus", sa.Integer(), nullable=False, server_default="0"))
    if "price_stars" not in columns:
        op.add_column("star_products", sa.Column("price_stars", sa.Integer(), nullable=True))
    if "price_usd" not in columns:
        op.add_column("star_products", sa.Column("price_usd", sa.Numeric(12, 2), nullable=True))

    # Preserve existing behavior: base credits start from the legacy credits_amount.
    op.execute("UPDATE star_products SET credits_base = COALESCE(credits_base, credits_amount)")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("star_products")}

    if "price_usd" in columns:
        op.drop_column("star_products", "price_usd")
    if "price_stars" in columns:
        op.drop_column("star_products", "price_stars")
    if "credits_bonus" in columns:
        op.drop_column("star_products", "credits_bonus")
    if "credits_base" in columns:
        op.drop_column("star_products", "credits_base")
