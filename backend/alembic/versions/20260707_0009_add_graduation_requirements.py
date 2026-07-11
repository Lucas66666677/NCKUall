"""Add graduation requirements for diagnosis reports.

Revision ID: 20260707_0009
Revises: 20260705_0008
Create Date: 2026-07-07
"""

from collections.abc import Sequence

from alembic import op
import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260707_0009"
down_revision: str | Sequence[str] | None = "20260705_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamp_columns() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def upgrade() -> None:
    op.create_table(
        "graduation_requirements",
        sa.Column(
            "department_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("curriculum_year", sa.Integer(), nullable=True),
        sa.Column(
            "total_required_credits",
            sa.Numeric(precision=5, scale=1),
            nullable=False,
        ),
        sa.Column(
            "major_required_credits",
            sa.Numeric(precision=5, scale=1),
            nullable=False,
        ),
        sa.Column(
            "major_elective_credits",
            sa.Numeric(precision=5, scale=1),
            nullable=False,
        ),
        sa.Column(
            "general_education_credits",
            sa.Numeric(precision=5, scale=1),
            nullable=False,
        ),
        sa.Column(
            "general_education_areas",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "rules_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.Vector(dim=1536),
            nullable=True,
        ),
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "department_id",
            "curriculum_year",
            name="uq_graduation_requirements_department_year",
        ),
    )
    op.create_index(
        "ix_graduation_requirements_department_active",
        "graduation_requirements",
        ["department_id", "is_active"],
        unique=False,
    )
    op.create_index(
        op.f("ix_graduation_requirements_department_id"),
        "graduation_requirements",
        ["department_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_graduation_requirements_department_id"),
        table_name="graduation_requirements",
    )
    op.drop_index(
        "ix_graduation_requirements_department_active",
        table_name="graduation_requirements",
    )
    op.drop_table("graduation_requirements")
