"""add support message media fields

Revision ID: 0005_support_message_media
Revises: 0004_flexible_topup_packages
Create Date: 2026-02-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_support_message_media"
down_revision = "0004_flexible_topup_packages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("support_messages")}

    if "media_type" not in columns:
        op.add_column("support_messages", sa.Column("media_type", sa.String(length=16), nullable=True))
    if "media_path" not in columns:
        op.add_column("support_messages", sa.Column("media_path", sa.String(length=512), nullable=True))
    if "media_file_name" not in columns:
        op.add_column("support_messages", sa.Column("media_file_name", sa.String(length=255), nullable=True))
    if "media_mime_type" not in columns:
        op.add_column("support_messages", sa.Column("media_mime_type", sa.String(length=128), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("support_messages")}

    if "media_mime_type" in columns:
        op.drop_column("support_messages", "media_mime_type")
    if "media_file_name" in columns:
        op.drop_column("support_messages", "media_file_name")
    if "media_path" in columns:
        op.drop_column("support_messages", "media_path")
    if "media_type" in columns:
        op.drop_column("support_messages", "media_type")
