"""add support chat tables

Revision ID: 0003_support_chats
Revises: 0002_admin_settings_and_provider_costs
Create Date: 2026-02-08
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0003_support_chats'
down_revision = '0002_admin_settings_and_provider_costs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'support_threads',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='open'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.UniqueConstraint('user_id', name='uq_support_thread_user'),
    )
    op.create_index('ix_support_threads_user_id', 'support_threads', ['user_id'])

    op.create_table(
        'support_messages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('thread_id', sa.Integer(), nullable=False),
        sa.Column('sender_type', sa.String(length=16), nullable=False),
        sa.Column('sender_admin_id', sa.BigInteger(), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('tg_message_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['thread_id'], ['support_threads.id']),
    )
    op.create_index('ix_support_messages_thread_id', 'support_messages', ['thread_id'])


def downgrade() -> None:
    op.drop_index('ix_support_messages_thread_id', table_name='support_messages')
    op.drop_table('support_messages')
    op.drop_index('ix_support_threads_user_id', table_name='support_threads')
    op.drop_table('support_threads')
