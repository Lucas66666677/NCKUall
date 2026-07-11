# Hybrid retrieval and cross-encoder reranking

`POST /api/chat` now uses the following retrieval pipeline:

1. Apply the department and optional category filters to both retrieval lanes.
2. Run PostgreSQL weighted full-text search while the embedding provider
   computes the query vector.
3. Run pgvector cosine search after the embedding is available.
4. Fuse both ranked lists with weighted Reciprocal Rank Fusion (RRF).
5. Keep the top 20 fused candidates.
6. Run FlashRank in a worker thread and keep the top 4 chunks for the LLM.

Raw vector distance and `ts_rank_cd` values are never directly mixed. RRF uses
rank positions, so score scales from the two retrieval systems do not need to
be calibrated.

## Database migration

```powershell
cd backend
alembic upgrade head
```

Migration `20260705_0006` creates a concurrent weighted GIN expression index:

- `source_title`: weight A
- `content`: weight B
- `metadata_json`: weight C
- text search configuration: `simple`

It also creates trigram GIN indexes for `source_title` and `content`.
PostgreSQL's built-in parser does not reliably segment every Traditional
Chinese name, so the lexical lane applies an exact substring boost for course
codes, quoted plan names, and names followed by `教授` or `老師`.

The query expression and index expression must remain identical or PostgreSQL
may not use the GIN index. Confirm production plans with:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id
FROM career_document_chunks
WHERE (
  setweight(
    to_tsvector('simple', COALESCE(source_title, '')),
    'A'
  )
  ||
  setweight(
    to_tsvector('simple', COALESCE(content, '')),
    'B'
  )
  ||
  setweight(
    to_tsvector(
      'simple',
      COALESCE(CAST(metadata_json AS TEXT), '')
    ),
    'C'
  )
) @@ websearch_to_tsquery('simple', 'DPS5001 王教授');
```

## Reranker

The default `ms-marco-MultiBERT-L-12` FlashRank model supports multilingual
content and is more appropriate for Traditional Chinese than the 4 MB English
TinyBERT default. The Docker build downloads it into the image, and each
Gunicorn worker preloads its ONNX session during FastAPI startup.

FlashRank inference is synchronous CPU work. `rerank_chunks()` runs it through
`asyncio.to_thread`, limits per-worker concurrency with a semaphore, and
enforces a timeout so the FastAPI event loop remains responsive.

For a memory-constrained environment, use:

```dotenv
RAG_RERANK_MODEL=ms-marco-TinyBERT-L-2-v2
```

That model is much smaller but should be benchmarked carefully on Chinese
queries before production use.

## Recommended production settings

```dotenv
RAG_RETRIEVAL_LANE_LIMIT=30
RAG_RRF_CANDIDATE_LIMIT=20
RAG_RRF_K=60
RAG_VECTOR_WEIGHT=1.0
RAG_LEXICAL_WEIGHT=1.15
RAG_CONTEXT_LIMIT=4
RAG_RERANK_MODEL=ms-marco-MultiBERT-L-12
RAG_RERANK_PRELOAD=true
RAG_RERANK_MAX_LENGTH=256
RAG_RERANK_MAX_CONCURRENCY=2
RAG_RERANK_TIMEOUT_SECONDS=8
RAG_RERANK_FAIL_OPEN=false
```

Keep `RAG_RERANK_FAIL_OPEN=false` when grounding quality is mandatory. If it
is enabled, a timeout or model failure falls back to RRF order.

Track vector latency, FTS latency, rerank latency, candidate overlap,
rerank score distribution, retrieval hit rate, and total chat p95/p99 before
changing weights or candidate counts.
