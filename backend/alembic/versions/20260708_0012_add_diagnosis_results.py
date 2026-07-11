"""add diagnosis results

Revision ID: 20260708_0012
Revises: 20260708_0011
Create Date: 2026-07-08
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260708_0012"
down_revision = "20260708_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "diagnosis_results",
        sa.Column("session_id", sa.String(length=120), nullable=True),
        sa.Column("owner_user_id", sa.String(length=160), nullable=True),
        sa.Column("owner_email_hash", sa.String(length=64), nullable=True),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("current_semester", sa.String(length=40), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_diagnosis_results_department_created",
        "diagnosis_results",
        ["department_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_diagnosis_results_owner_created",
        "diagnosis_results",
        ["owner_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_diagnosis_results_owner_email_hash",
        "diagnosis_results",
        ["owner_email_hash"],
        unique=False,
    )
    op.create_index(
        "ix_diagnosis_results_owner_user_id",
        "diagnosis_results",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_diagnosis_results_session_created",
        "diagnosis_results",
        ["session_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_diagnosis_results_session_id",
        "diagnosis_results",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_diagnosis_results_session_id", table_name="diagnosis_results")
    op.drop_index("ix_diagnosis_results_session_created", table_name="diagnosis_results")
    op.drop_index("ix_diagnosis_results_owner_user_id", table_name="diagnosis_results")
    op.drop_index("ix_diagnosis_results_owner_email_hash", table_name="diagnosis_results")
    op.drop_index("ix_diagnosis_results_owner_created", table_name="diagnosis_results")
    op.drop_index("ix_diagnosis_results_department_created", table_name="diagnosis_results")
    op.drop_table("diagnosis_results")
