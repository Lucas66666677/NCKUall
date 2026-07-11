from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


logger = logging.getLogger(__name__)

EXPECTED_VECTOR_INDEXES = frozenset(
    {
        "ix_courses_embedding_hnsw_cosine",
        "ix_course_reviews_embedding_hnsw_cosine",
        "ix_career_resources_embedding_hnsw_cosine",
        "ix_career_resource_reviews_embedding_hnsw_cosine",
        "ix_career_document_chunks_embedding_hnsw_cosine",
        "ix_activities_embedding_hnsw_cosine",
        "ix_life_resources_embedding_hnsw_cosine",
        "ix_life_reviews_embedding_hnsw_cosine",
    }
)

INDEX_HEALTH_SQL = text(
    """
    SELECT
        index_class.relname AS index_name,
        index_data.indisvalid AS is_valid,
        index_data.indisready AS is_ready,
        access_method.amname AS index_method,
        pg_get_indexdef(index_data.indexrelid) AS definition
    FROM pg_index AS index_data
    JOIN pg_class AS index_class
      ON index_class.oid = index_data.indexrelid
    JOIN pg_class AS table_class
      ON table_class.oid = index_data.indrelid
    JOIN pg_namespace AS table_schema
      ON table_schema.oid = table_class.relnamespace
    JOIN pg_am AS access_method
      ON access_method.oid = index_class.relam
    WHERE table_schema.nspname = current_schema()
      AND index_class.relname LIKE 'ix_%_embedding_hnsw_cosine'
    """
)


async def check_vector_indexes(engine: AsyncEngine) -> bool:
    """Log missing or invalid HNSW cosine indexes without changing schema."""

    async with engine.connect() as connection:
        rows = (await connection.execute(INDEX_HEALTH_SQL)).mappings().all()

    indexes = {str(row["index_name"]): row for row in rows}
    problems: list[str] = []

    for index_name in sorted(EXPECTED_VECTOR_INDEXES):
        row = indexes.get(index_name)
        if row is None:
            problems.append(f"{index_name}: missing")
            continue

        definition = str(row["definition"]).lower()
        if not row["is_valid"] or not row["is_ready"]:
            problems.append(f"{index_name}: invalid or unfinished")
        elif row["index_method"] != "hnsw":
            problems.append(f"{index_name}: expected hnsw")
        elif "vector_cosine_ops" not in definition:
            problems.append(f"{index_name}: expected vector_cosine_ops")

    if problems:
        logger.warning("Vector index health check failed: %s", "; ".join(problems))
        return False

    logger.info("All %d HNSW cosine indexes are ready.", len(EXPECTED_VECTOR_INDEXES))
    return True
