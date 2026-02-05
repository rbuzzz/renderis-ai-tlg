"""initial schema

Revision ID: 0001_initial
Revises: 
Create Date: 2026-02-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(length=255), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('is_banned', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('balance_credits', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('referral_discount_pct', sa.Integer(), nullable=True),
        sa.Column('referral_code_applied', sa.String(length=64), nullable=True),
        sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint('telegram_id', name='uq_users_telegram_id'),
    )
    op.create_index('ix_users_telegram_id', 'users', ['telegram_id'])

    op.create_table(
        'credit_ledger',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('delta_credits', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=64), nullable=False),
        sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('idempotency_key', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('idempotency_key', name='uq_credit_ledger_idempotency_key'),
    )
    op.create_index('ix_credit_ledger_user_id', 'credit_ledger', ['user_id'])

    op.create_table(
        'prices',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('model_key', sa.String(length=64), nullable=False),
        sa.Column('option_key', sa.String(length=64), nullable=False),
        sa.Column('price_credits', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('model_type', sa.String(length=32), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.UniqueConstraint('model_key', 'option_key', name='uq_prices_model_option'),
    )
    op.create_index('ix_prices_model_key', 'prices', ['model_key'])
    op.create_index('ix_prices_option_key', 'prices', ['option_key'])

    op.create_table(
        'referral_codes',
        sa.Column('code', sa.String(length=32), primary_key=True),
        sa.Column('discount_pct', sa.Integer(), nullable=False),
        sa.Column('created_by_admin_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('usage_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
    )

    op.create_table(
        'referral_uses',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('code', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['code'], ['referral_codes.code'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_referral_uses_code', 'referral_uses', ['code'])
    op.create_index('ix_referral_uses_user_id', 'referral_uses', ['user_id'])

    op.create_table(
        'promo_codes',
        sa.Column('code', sa.String(length=32), primary_key=True),
        sa.Column('credits_amount', sa.Integer(), nullable=False),
        sa.Column('created_by_admin_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('redeemed_by_user_id', sa.Integer(), nullable=True),
        sa.Column('redeemed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('batch_id', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['redeemed_by_user_id'], ['users.id'], ondelete='SET NULL'),
    )

    op.create_table(
        'star_products',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('title', sa.String(length=128), nullable=False),
        sa.Column('stars_amount', sa.Integer(), nullable=False),
        sa.Column('credits_amount', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
    )

    op.create_table(
        'orders',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('telegram_payment_charge_id', sa.String(length=128), nullable=False),
        sa.Column('provider_payment_charge_id', sa.String(length=128), nullable=False),
        sa.Column('payload', sa.String(length=128), nullable=False),
        sa.Column('stars_amount', sa.Integer(), nullable=False),
        sa.Column('credits_amount', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('telegram_payment_charge_id', name='uq_orders_telegram_payment_charge_id'),
    )
    op.create_index('ix_orders_user_id', 'orders', ['user_id'])

    op.create_table(
        'generations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('generation_order_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('model', sa.String(length=64), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('options', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('outputs_requested', sa.Integer(), nullable=False),
        sa.Column('total_cost_credits', sa.Integer(), nullable=False),
        sa.Column('discount_pct', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('final_cost_credits', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('generation_order_id', name='uq_generations_order_id'),
    )
    op.create_index('ix_generations_user_id', 'generations', ['user_id'])

    op.create_table(
        'generation_tasks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('generation_id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.String(length=128), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('result_urls', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('fail_code', sa.String(length=64), nullable=True),
        sa.Column('fail_msg', sa.String(length=255), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('raw_response', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(['generation_id'], ['generations.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_generation_tasks_generation_id', 'generation_tasks', ['generation_id'])
    op.create_index('ix_generation_tasks_task_id', 'generation_tasks', ['task_id'])


def downgrade() -> None:
    op.drop_index('ix_generation_tasks_task_id', table_name='generation_tasks')
    op.drop_index('ix_generation_tasks_generation_id', table_name='generation_tasks')
    op.drop_table('generation_tasks')
    op.drop_index('ix_generations_user_id', table_name='generations')
    op.drop_table('generations')
    op.drop_index('ix_orders_user_id', table_name='orders')
    op.drop_table('orders')
    op.drop_table('star_products')
    op.drop_table('promo_codes')
    op.drop_index('ix_referral_uses_user_id', table_name='referral_uses')
    op.drop_index('ix_referral_uses_code', table_name='referral_uses')
    op.drop_table('referral_uses')
    op.drop_table('referral_codes')
    op.drop_index('ix_prices_option_key', table_name='prices')
    op.drop_index('ix_prices_model_key', table_name='prices')
    op.drop_table('prices')
    op.drop_index('ix_credit_ledger_user_id', table_name='credit_ledger')
    op.drop_table('credit_ledger')
    op.drop_index('ix_users_telegram_id', table_name='users')
    op.drop_table('users')
