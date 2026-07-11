# pgvector HNSW indexes

The application stores `vector(1536)` embeddings and orders RAG candidates by
cosine distance (`embedding <=> query_vector`). The matching PostgreSQL operator
class is therefore `vector_cosine_ops`.

## Core index

For `career_document_chunks`, the exact balanced configuration is:

```sql
CREATE INDEX CONCURRENTLY ix_career_document_chunks_embedding_hnsw_cosine
ON career_document_chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

`CONCURRENTLY` reduces write blocking on a live table, but PostgreSQL requires
it to run outside a transaction. Alembic revision `20260702_0002` handles that
with an autocommit block and creates the equivalent index on every current
embedding column.

For a new or maintenance-window database where locking is acceptable, omit
`CONCURRENTLY`:

```sql
CREATE INDEX ix_career_document_chunks_embedding_hnsw_cosine
ON career_document_chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

Apply the checked-in migration:

```powershell
cd backend
alembic upgrade head
```

Do not run both the Alembic migration and `scripts/vector_indexes.sql` on the
same database. The SQL file is an alternative for manual Supabase SQL Editor
work and includes a health query.

If a concurrent build is interrupted, PostgreSQL can leave an invalid index.
The startup health check reports it. Drop that index, then rerun the migration:

```sql
DROP INDEX CONCURRENTLY IF EXISTS
    ix_career_document_chunks_embedding_hnsw_cosine;
```

## Dimensions

The current SQLAlchemy schema uses `vector(1536)`. OpenAI
`text-embedding-3-small` produces 1536 dimensions by default, and the backend
now requests 1536 explicitly.

Google embedding models must also be configured to output exactly 1536
dimensions. The backend passes `output_dimensionality=1536` to
`GoogleGenerativeAIEmbeddings`. A 768-dimensional embedding cannot be inserted
into `vector(1536)`, and embeddings from different models must not be mixed
even when their lengths happen to match.

pgvector HNSW supports `vector` indexes up to 2,000 dimensions, so 1536 can use
the direct index shown above. A model output above 2,000 dimensions requires a
different design, such as explicitly requesting 1536 dimensions or indexing a
cast to `halfvec`, whose HNSW limit is 4,000 dimensions:

```sql
CREATE INDEX CONCURRENTLY example_embedding_halfvec_hnsw
ON example_table
USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);
```

The query must use the same cast and distance operator for that expression
index to be eligible.

## Tuning `m` and `ef_construction`

The pgvector defaults are `m = 16` and `ef_construction = 64`. They are the
recommended starting point, not universal optimum values.

- `m` is the maximum number of graph connections per layer. Increasing it
  generally improves graph connectivity and recall, especially for large or
  difficult datasets. It also increases index size, build memory, build time,
  and ongoing insert/update cost.
- `ef_construction` is the candidate-list size used while building each graph
  node. Increasing it usually produces a higher-quality graph and better
  recall, but makes initial builds and later inserts slower and consumes more
  working memory. It does not replace query-time tuning.

Practical starting profiles:

| Profile | `m` | `ef_construction` | Use case |
| --- | ---: | ---: | --- |
| Lower resource / fast ingestion | 8-12 | 32-64 | Early development or write-heavy ingestion |
| Balanced default | 16 | 64 | Start here for this application |
| Higher recall | 24 | 128 | Read-heavy production after benchmarking |
| Very high recall | 32 | 200 | Large dataset with enough RAM and slower writes acceptable |

Raise one setting at a time and benchmark against an exact-search ground-truth
sample. Measure recall@k, p95/p99 latency, index build time, index size, and
insert throughput. Increasing values blindly can exhaust Supabase memory
without producing a useful recall improvement.

Query-time recall is controlled separately by `hnsw.ef_search` (default 40).
For a transaction that needs higher recall:

```sql
BEGIN;
SET LOCAL hnsw.ef_search = 100;

SELECT id, content, embedding <=> :query_embedding AS distance
FROM career_document_chunks
WHERE department_id = :department_id
  AND embedding IS NOT NULL
ORDER BY embedding <=> :query_embedding
LIMIT 6;

COMMIT;
```

The `ORDER BY` expression must be the raw distance operator in ascending order
with a `LIMIT`; ordering by a transformed similarity expression may prevent the
planner from using the HNSW index.

Department/category pre-filtering can remove candidates returned by an
approximate scan. With pgvector 0.8 or later, iterative scans can continue until
enough filtered rows are found:

```sql
SET LOCAL hnsw.iterative_scan = 'strict_order';
```

Use `EXPLAIN (ANALYZE, BUFFERS)` on representative production-like queries to
confirm an `Index Scan` uses the expected HNSW index. PostgreSQL may reasonably
choose a sequential scan for very small tables or highly selective filters.

## Startup health check

Schema changes should not run independently in every Gunicorn worker. Enable
the read-only startup check instead:

```env
CHECK_VECTOR_INDEXES_ON_STARTUP=true
```

It verifies that all expected indexes exist, are ready and valid, use HNSW, and
contain `vector_cosine_ops`. Problems are logged without preventing the API
from starting. Actual creation remains a single release step through Alembic.
