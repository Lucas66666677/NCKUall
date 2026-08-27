"""add life review flags

Revision ID: 20260826_0014
Revises: 20260708_0013
Create Date: 2026-08-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260826_0014"
down_revision = "20260708_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "life_review_flags",
        sa.Column("life_review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reporter_user_id", sa.String(length=160), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["life_review_id"], ["life_reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reporter_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "life_review_id",
            "reporter_user_id",
            name="uq_life_review_flags_review_reporter",
        ),
    )
    op.create_index(
        "ix_life_review_flags_life_review_id",
        "life_review_flags",
        ["life_review_id"],
        unique=False,
    )
    op.create_index(
        "ix_life_review_flags_review_created",
        "life_review_flags",
        ["life_review_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_life_review_flags_reporter_user_id",
        "life_review_flags",
        ["reporter_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_life_review_flags_reporter_user_id", table_name="life_review_flags")
    op.drop_index("ix_life_review_flags_review_created", table_name="life_review_flags")
    op.drop_index("ix_life_review_flags_life_review_id", table_name="life_review_flags")
    op.drop_table("life_review_flags")
