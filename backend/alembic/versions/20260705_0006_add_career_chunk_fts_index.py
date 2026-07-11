"""Add weighted full-text index for hybrid RAG retrieval.

Revision ID: 20260705_0006
Revises: 20260705_0005
Create Date: 2026-07-05
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260705_0006"
down_revision: str | Sequence[str] | None = "20260705_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_career_document_chunks_weighted_fts"
TITLE_TRGM_INDEX = "ix_career_document_chunks_source_title_trgm"
CONTENT_TRGM_INDEX = "ix_career_document_chunks_content_trgm"


def upgrade() -> None:
    """Build the GIN index without blocking writes to the live corpus."""

    with op.get_context().autocommit_block():
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY {INDEX_NAME}
            ON career_document_chunks
            USING gin (
                (
                    setweight(
                        to_tsvector(
                            'simple'::regconfig,
                            COALESCE(source_title, '')
                        ),
                        'A'
                    )
                    ||
                    setweight(
                        to_tsvector(
                            'simple'::regconfig,
                            COALESCE(content, '')
                        ),
                        'B'
                    )
                    ||
                    setweight(
                        to_tsvector(
                            'simple'::regconfig,
                            COALESCE(CAST(metadata_json AS TEXT), '')
                        ),
                        'C'
                    )
                )
            )
            """
        )
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY {TITLE_TRGM_INDEX}
            ON career_document_chunks
            USING gin (source_title gin_trgm_ops)
            """
        )
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY {CONTENT_TRGM_INDEX}
            ON career_document_chunks
            USING gin (content gin_trgm_ops)
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            f"{CONTENT_TRGM_INDEX}"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            f"{TITLE_TRGM_INDEX}"
        )
        op.execute(
            f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}"
        )
