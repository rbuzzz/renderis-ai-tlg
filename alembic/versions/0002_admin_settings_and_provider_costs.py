"""add app settings and provider costs

Revision ID: 0002_admin_settings_and_provider_costs
Revises: 0001_initial
Create Date: 2026-02-06
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0002_admin_settings_and_provider_costs'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('prices', sa.Column('provider_credits', sa.Integer(), nullable=True))
    op.add_column('prices', sa.Column('provider_cost_usd', sa.Numeric(12, 6), nullable=True))

    op.create_table(
        'app_settings',
        sa.Column('key', sa.String(length=64), primary_key=True),
        sa.Column('value', sa.String(length=255), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('app_settings')
    op.drop_column('prices', 'provider_cost_usd')
    op.drop_column('prices', 'provider_credits')
