"""Add pg_trgm indexes for typeahead and fuzzy search.

Revision ID: 20260705_0005
Revises: 20260703_0004
Create Date: 2026-07-05
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260705_0005"
down_revision: str | Sequence[str] | None = "20260703_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_courses_title_zh_trgm",
        "courses",
        ["title_zh"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"title_zh": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_courses_instructor_name_trgm",
        "courses",
        ["instructor_name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"instructor_name": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_activities_title_trgm",
        "activities",
        ["title"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_activities_title_trgm",
        table_name="activities",
    )
    op.drop_index(
        "ix_courses_instructor_name_trgm",
        table_name="courses",
    )
    op.drop_index(
        "ix_courses_title_zh_trgm",
        table_name="courses",
    )
