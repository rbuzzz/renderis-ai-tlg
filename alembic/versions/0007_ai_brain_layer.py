"""add ai brain config, logs and improvement balances

Revision ID: 0007_ai_brain_layer
Revises: 0006_admin_change_requests
Create Date: 2026-02-12
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0007_ai_brain_layer"
down_revision = "0006_admin_change_requests"
branch_labels = None
depends_on = None


DEFAULT_SYSTEM_PROMPT = (
    "You are a professional AI prompt engineer. Improve the user's prompt to be more detailed, "
    "cinematic, structured, and optimized for image generation models. "
    "Do not add unrelated concepts. Keep original intent."
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "ai_brain_config" not in tables:
        op.create_table(
            "ai_brain_config",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("openai_model", sa.String(length=128), nullable=False, server_default="gpt-4o-mini"),
            sa.Column("temperature", sa.Numeric(4, 2), nullable=False, server_default="0.70"),
            sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="600"),
            sa.Column("price_per_improve", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("daily_limit_per_user", sa.Integer(), nullable=False, server_default="20"),
            sa.Column("pack_price_credits", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("pack_size_improvements", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("system_prompt", sa.Text(), nullable=False, server_default=DEFAULT_SYSTEM_PROMPT),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    ai_brain_config_count = bind.execute(sa.text("SELECT COUNT(*) FROM ai_brain_config")).scalar() or 0
    if int(ai_brain_config_count) == 0:
        bind.execute(
            sa.text(
                """
                INSERT INTO ai_brain_config (
                    id, enabled, openai_model, temperature, max_tokens,
                    price_per_improve, daily_limit_per_user, pack_price_credits, pack_size_improvements,
                    system_prompt, created_at, updated_at
                )
                VALUES (
                    1, false, 'gpt-4o-mini', 0.70, 600,
                    1, 20, 3, 10,
                    :system_prompt, NOW(), NOW()
                )
                """
            ),
            {"system_prompt": DEFAULT_SYSTEM_PROMPT},
        )

    if "ai_improvement_balances" not in tables:
        op.create_table(
            "ai_improvement_balances",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("remaining_improvements", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", name="uq_ai_improvement_balances_user_id"),
        )
        op.create_index("ix_ai_improvement_balances_user_id", "ai_improvement_balances", ["user_id"])

    if "ai_brain_logs" not in tables:
        op.create_table(
            "ai_brain_logs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("action", sa.String(length=32), nullable=False, server_default="improve_prompt"),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("source", sa.String(length=16), nullable=False, server_default="none"),
            sa.Column("spent_credits", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("prompt_original", sa.Text(), nullable=False),
            sa.Column("prompt_result", sa.Text(), nullable=True),
            sa.Column("model", sa.String(length=128), nullable=False),
            sa.Column("temperature", sa.Numeric(4, 2), nullable=False, server_default="0.70"),
            sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="600"),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column(
                "meta",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_ai_brain_logs_user_id", "ai_brain_logs", ["user_id"])
        op.create_index("ix_ai_brain_logs_created_at", "ai_brain_logs", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "ai_brain_logs" in tables:
        index_names = {idx.get("name") for idx in inspector.get_indexes("ai_brain_logs")}
        if "ix_ai_brain_logs_created_at" in index_names:
            op.drop_index("ix_ai_brain_logs_created_at", table_name="ai_brain_logs")
        if "ix_ai_brain_logs_user_id" in index_names:
            op.drop_index("ix_ai_brain_logs_user_id", table_name="ai_brain_logs")
        op.drop_table("ai_brain_logs")

    if "ai_improvement_balances" in tables:
        index_names = {idx.get("name") for idx in inspector.get_indexes("ai_improvement_balances")}
        if "ix_ai_improvement_balances_user_id" in index_names:
            op.drop_index("ix_ai_improvement_balances_user_id", table_name="ai_improvement_balances")
        op.drop_table("ai_improvement_balances")

    if "ai_brain_config" in tables:
        op.drop_table("ai_brain_config")

