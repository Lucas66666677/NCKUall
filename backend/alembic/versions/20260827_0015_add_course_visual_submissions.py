"""add course visual submissions

Revision ID: 20260827_0015
Revises: 20260826_0014
Create Date: 2026-08-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260827_0015"
down_revision = "20260826_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    course_submission_status = postgresql.ENUM(
        "PENDING",
        "APPROVED",
        "REJECTED",
        name="course_submission_status",
        # The migration creates this type explicitly below; prevent table
        # creation from issuing a second CREATE TYPE in the same upgrade.
        create_type=False,
    )
    course_submission_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "course_visual_submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submitted_by_user_id", sa.String(length=160), nullable=True),
        sa.Column(
            "status",
            course_submission_status,
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("proposed", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("upload_sha256", sa.String(length=64), nullable=True),
        sa.Column("reviewed_by_user_id", sa.String(length=160), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_course_visual_submissions_course_id",
        "course_visual_submissions",
        ["course_id"],
        unique=False,
    )
    op.create_index(
        "ix_course_visual_submissions_submitted_by_user_id",
        "course_visual_submissions",
        ["submitted_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_course_visual_submissions_upload_sha256",
        "course_visual_submissions",
        ["upload_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_course_visual_submissions_status_created",
        "course_visual_submissions",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_course_visual_submissions_status_created",
        table_name="course_visual_submissions",
    )
    op.drop_index(
        "ix_course_visual_submissions_upload_sha256",
        table_name="course_visual_submissions",
    )
    op.drop_index(
        "ix_course_visual_submissions_submitted_by_user_id",
        table_name="course_visual_submissions",
    )
    op.drop_index(
        "ix_course_visual_submissions_course_id",
        table_name="course_visual_submissions",
    )
    op.drop_table("course_visual_submissions")
    postgresql.ENUM(name="course_submission_status").drop(
        op.get_bind(), checkfirst=True
    )
