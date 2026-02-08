"""add model latency stats and generation progress message

Revision ID: 0004_model_latency_and_progress
Revises: 0003_support_chats
Create Date: 2026-02-08
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0004_model_latency_and_progress'
down_revision = '0003_support_chats'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('generations', sa.Column('progress_message_id', sa.Integer(), nullable=True))

    op.create_table(
        'model_latency_stats',
        sa.Column('model_key', sa.String(length=64), primary_key=True),
        sa.Column('avg_seconds', sa.Numeric(precision=10, scale=2), nullable=False, server_default='0'),
        sa.Column('sample_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('model_latency_stats')
    op.drop_column('generations', 'progress_message_id')
