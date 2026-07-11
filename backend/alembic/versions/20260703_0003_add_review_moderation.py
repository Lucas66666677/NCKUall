"""Add review reports and moderation workflow.

Revision ID: 20260703_0003
Revises: 20260702_0002
Create Date: 2026-07-03
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260703_0003"
down_revision: str | Sequence[str] | None = "20260702_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    moderation_status = postgresql.ENUM(
        "APPROVED",
        "HIDDEN",
        "PENDING",
        name="review_moderation_status",
        create_type=False,
    )
    moderation_status.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "life_reviews",
        sa.Column(
            "moderation_status",
            moderation_status,
            server_default="APPROVED",
            nullable=False,
        ),
    )
    op.add_column(
        "life_reviews",
        sa.Column(
            "report_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "life_reviews",
        sa.Column(
            "last_reported_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "life_reviews",
        sa.Column(
            "moderated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "life_reviews",
        sa.Column("moderated_by", sa.String(length=120), nullable=True),
    )
    op.create_index(
        "ix_life_reviews_moderation_status",
        "life_reviews",
        ["moderation_status"],
        unique=False,
    )
    op.create_index(
        "ix_life_reviews_moderation_reports",
        "life_reviews",
        ["moderation_status", "report_count"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_life_reviews_moderation_reports",
        table_name="life_reviews",
    )
    op.drop_index(
        "ix_life_reviews_moderation_status",
        table_name="life_reviews",
    )
    op.drop_column("life_reviews", "moderated_by")
    op.drop_column("life_reviews", "moderated_at")
    op.drop_column("life_reviews", "last_reported_at")
    op.drop_column("life_reviews", "report_count")
    op.drop_column("life_reviews", "moderation_status")
    sa.Enum(name="review_moderation_status").drop(
        op.get_bind(),
        checkfirst=True,
    )
