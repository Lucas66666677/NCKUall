"""add karma engine

Revision ID: 20260708_0013
Revises: 20260708_0012
Create Date: 2026-07-08
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260708_0013"
down_revision = "20260708_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=160), nullable=False),
        sa.Column("email_hash", sa.String(length=64), nullable=True),
        sa.Column("karma_points", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email_hash", "users", ["email_hash"], unique=False)

    for table_name in ("course_reviews", "life_reviews"):
        op.add_column(table_name, sa.Column("author_user_id", sa.String(length=160), nullable=True))
        op.add_column(
            table_name,
            sa.Column("is_approved", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        )
        op.add_column(
            table_name,
            sa.Column("score", sa.Numeric(precision=10, scale=4), server_default="0", nullable=False),
        )
        op.add_column(
            table_name,
            sa.Column(
                "ai_spam_confidence",
                sa.Numeric(precision=5, scale=4),
                server_default="0",
                nullable=False,
            ),
        )
        op.create_foreign_key(
            f"fk_{table_name}_author_user_id_users",
            table_name,
            "users",
            ["author_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(f"ix_{table_name}_author_user_id", table_name, ["author_user_id"], unique=False)
        op.create_index(f"ix_{table_name}_is_approved", table_name, ["is_approved"], unique=False)

    op.create_table(
        "life_review_votes",
        sa.Column("life_review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("voter_user_id", sa.String(length=160), nullable=False),
        sa.Column("value", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["life_review_id"], ["life_reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["voter_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "life_review_id",
            "voter_user_id",
            name="uq_life_review_votes_review_voter",
        ),
    )
    op.create_index(
        "ix_life_review_votes_life_review_id",
        "life_review_votes",
        ["life_review_id"],
        unique=False,
    )
    op.create_index(
        "ix_life_review_votes_review_created",
        "life_review_votes",
        ["life_review_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_life_review_votes_voter_user_id",
        "life_review_votes",
        ["voter_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_life_review_votes_voter_user_id", table_name="life_review_votes")
    op.drop_index("ix_life_review_votes_review_created", table_name="life_review_votes")
    op.drop_index("ix_life_review_votes_life_review_id", table_name="life_review_votes")
    op.drop_table("life_review_votes")

    for table_name in ("life_reviews", "course_reviews"):
        op.drop_index(f"ix_{table_name}_is_approved", table_name=table_name)
        op.drop_index(f"ix_{table_name}_author_user_id", table_name=table_name)
        op.drop_constraint(
            f"fk_{table_name}_author_user_id_users",
            table_name,
            type_="foreignkey",
        )
        op.drop_column(table_name, "ai_spam_confidence")
        op.drop_column(table_name, "score")
        op.drop_column(table_name, "is_approved")
        op.drop_column(table_name, "author_user_id")

    op.drop_index("ix_users_email_hash", table_name="users")
    op.drop_table("users")
