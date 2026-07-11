"""Add privacy-preserving anonymous resource analytics.

Revision ID: 20260703_0004
Revises: 20260703_0003
Create Date: 2026-07-03
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260703_0004"
down_revision: str | Sequence[str] | None = "20260703_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_logs",
        sa.Column(
            "resource_type",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "resource_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_search_logs_type_created_resource",
        "search_logs",
        ["resource_type", "created_at", "resource_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_search_logs_type_created_resource",
        table_name="search_logs",
    )
    op.drop_table("search_logs")
