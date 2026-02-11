"""add admin change request workflow tables

Revision ID: 0006_admin_change_requests
Revises: 0005_support_message_media
Create Date: 2026-02-11
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_admin_change_requests"
down_revision = "0005_support_message_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "admin_change_requests" not in tables:
        op.create_table(
            "admin_change_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
            sa.Column("change_type", sa.String(length=32), nullable=False),
            sa.Column("target_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("credits_amount", sa.Integer(), nullable=True),
            sa.Column("balance_value", sa.Integer(), nullable=True),
            sa.Column("promo_code", sa.String(length=32), nullable=True),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("created_by_role", sa.String(length=16), nullable=False, server_default="subadmin"),
            sa.Column("created_by_login", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reviewed_by_login", sa.String(length=255), nullable=True),
            sa.Column("reviewed_by_telegram_id", sa.BigInteger(), nullable=True),
            sa.Column("apply_error", sa.Text(), nullable=True),
        )
        op.create_index(
            "ix_admin_change_requests_status",
            "admin_change_requests",
            ["status"],
        )
        op.create_index(
            "ix_admin_change_requests_change_type",
            "admin_change_requests",
            ["change_type"],
        )
        op.create_index(
            "ix_admin_change_requests_target_user_id",
            "admin_change_requests",
            ["target_user_id"],
        )

    if "admin_change_comments" not in tables:
        op.create_table(
            "admin_change_comments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("request_id", sa.Integer(), sa.ForeignKey("admin_change_requests.id"), nullable=False),
            sa.Column("author_role", sa.String(length=16), nullable=False),
            sa.Column("author_login", sa.String(length=255), nullable=False),
            sa.Column("author_telegram_id", sa.BigInteger(), nullable=True),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_admin_change_comments_request_id",
            "admin_change_comments",
            ["request_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "admin_change_comments" in tables:
        for idx in inspector.get_indexes("admin_change_comments"):
            if idx.get("name") == "ix_admin_change_comments_request_id":
                op.drop_index("ix_admin_change_comments_request_id", table_name="admin_change_comments")
                break
        op.drop_table("admin_change_comments")

    if "admin_change_requests" in tables:
        index_names = {idx.get("name") for idx in inspector.get_indexes("admin_change_requests")}
        if "ix_admin_change_requests_target_user_id" in index_names:
            op.drop_index("ix_admin_change_requests_target_user_id", table_name="admin_change_requests")
        if "ix_admin_change_requests_change_type" in index_names:
            op.drop_index("ix_admin_change_requests_change_type", table_name="admin_change_requests")
        if "ix_admin_change_requests_status" in index_names:
            op.drop_index("ix_admin_change_requests_status", table_name="admin_change_requests")
        op.drop_table("admin_change_requests")
