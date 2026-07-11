"""Add provenance tags for visually ingested courses.

Revision ID: 20260705_0008
Revises: 20260705_0007
Create Date: 2026-07-05
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260705_0008"
down_revision: str | Sequence[str] | None = "20260705_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "courses",
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("courses", "tags")
