-- Run after enabling pgvector and applying the table migrations.
-- These statements use the balanced pgvector defaults explicitly.
-- For large live tables, add CONCURRENTLY after INDEX and run each statement
-- outside a transaction, or use Alembic revision 20260702_0002.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE INDEX IF NOT EXISTS ix_career_document_chunks_embedding_hnsw_cosine
ON career_document_chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS ix_courses_embedding_hnsw_cosine
ON courses
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS ix_course_reviews_embedding_hnsw_cosine
ON course_reviews
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS ix_career_resources_embedding_hnsw_cosine
ON career_resources
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS ix_career_resource_reviews_embedding_hnsw_cosine
ON career_resource_reviews
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS ix_activities_embedding_hnsw_cosine
ON activities
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS ix_life_resources_embedding_hnsw_cosine
ON life_resources
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS ix_life_reviews_embedding_hnsw_cosine
ON life_reviews
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Health check: every row should show hnsw, valid=true, ready=true and
-- vector_cosine_ops in index_definition.
SELECT
    index_class.relname AS index_name,
    access_method.amname AS index_method,
    index_data.indisvalid AS valid,
    index_data.indisready AS ready,
    pg_size_pretty(pg_relation_size(index_data.indexrelid)) AS index_size,
    pg_get_indexdef(index_data.indexrelid) AS index_definition
FROM pg_index AS index_data
JOIN pg_class AS index_class
  ON index_class.oid = index_data.indexrelid
JOIN pg_am AS access_method
  ON access_method.oid = index_class.relam
WHERE index_class.relname LIKE 'ix_%_embedding_hnsw_cosine'
ORDER BY index_class.relname;
