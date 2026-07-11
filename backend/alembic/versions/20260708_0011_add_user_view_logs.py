"""Add authenticated user view logs for recommendations.

Revision ID: 20260708_0011
Revises: 20260708_0010
Create Date: 2026-07-08
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260708_0011"
down_revision: str | Sequence[str] | None = "20260708_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_view_logs",
        sa.Column("user_id", sa.String(length=160), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "resource_type IN ('course', 'career', 'event')",
            name="ck_user_view_logs_resource_type",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_view_logs_user_id"),
        "user_view_logs",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_view_logs_user_created",
        "user_view_logs",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_user_view_logs_user_resource",
        "user_view_logs",
        ["user_id", "resource_type", "resource_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_view_logs_resource_created",
        "user_view_logs",
        ["resource_type", "resource_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_view_logs_resource_created",
        table_name="user_view_logs",
    )
    op.drop_index(
        "ix_user_view_logs_user_resource",
        table_name="user_view_logs",
    )
    op.drop_index(
        "ix_user_view_logs_user_created",
        table_name="user_view_logs",
    )
    op.drop_index(op.f("ix_user_view_logs_user_id"), table_name="user_view_logs")
    op.drop_table("user_view_logs")
