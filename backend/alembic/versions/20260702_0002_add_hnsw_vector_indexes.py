"""Add cosine HNSW indexes to embedding columns.

Revision ID: 20260702_0002
Revises: 20260702_0001
Create Date: 2026-07-02
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260702_0002"
down_revision: str | Sequence[str] | None = "20260702_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HNSW_INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_courses_embedding_hnsw_cosine", "courses"),
    ("ix_course_reviews_embedding_hnsw_cosine", "course_reviews"),
    ("ix_career_resources_embedding_hnsw_cosine", "career_resources"),
    (
        "ix_career_resource_reviews_embedding_hnsw_cosine",
        "career_resource_reviews",
    ),
    (
        "ix_career_document_chunks_embedding_hnsw_cosine",
        "career_document_chunks",
    ),
    ("ix_activities_embedding_hnsw_cosine", "activities"),
    ("ix_life_resources_embedding_hnsw_cosine", "life_resources"),
    ("ix_life_reviews_embedding_hnsw_cosine", "life_reviews"),
)


def upgrade() -> None:
    """
    Build ANN indexes without holding a long write lock on populated tables.

    CREATE INDEX CONCURRENTLY cannot run inside a transaction. Alembic's
    autocommit block deliberately commits the surrounding migration first.
    """

    with op.get_context().autocommit_block():
        for index_name, table_name in HNSW_INDEXES:
            op.execute(
                f"""
                CREATE INDEX CONCURRENTLY {index_name}
                ON {table_name}
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
                """
            )


def downgrade() -> None:
    """Remove HNSW indexes without blocking writes on populated tables."""

    with op.get_context().autocommit_block():
        for index_name, _table_name in reversed(HNSW_INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")
