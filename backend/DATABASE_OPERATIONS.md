# Database Operations Guide

This guide documents the production database posture for NCKUall's FastAPI,
SQLAlchemy 2.0, Supabase PostgreSQL, pgvector, and Alembic stack.

## Runtime Connection Pool

The web service uses SQLAlchemy's async engine with an async-adapted queue pool:

```text
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=50
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=1800
DB_POOL_PRE_PING=true
DB_POOL_USE_LIFO=true
```

These numbers are per process. With `WEB_CONCURRENCY=2`, the theoretical
maximum client connections from the API to the database or pooler is:

```text
workers * (DB_POOL_SIZE + DB_MAX_OVERFLOW)
= 2 * (20 + 50)
= 140 client connections
```

When using Supabase transaction pooling, these are client connections to the
pooler, not necessarily backend PostgreSQL sessions. Still, size them against
Supabase's pooler client limit and your plan's backend connection cap.

## Supabase Pooler Connection Strings

Use separate URLs for long-running API traffic and migration/seed jobs.

### FastAPI Web Service

For high traffic web requests, use the Supabase pooler in transaction mode
when your project needs many short-lived concurrent requests:

```text
DATABASE_URL=postgresql+psycopg://postgres.<PROJECT_REF>:<PASSWORD>@aws-0-<REGION>.pooler.supabase.com:6543/postgres
DB_POOLER_MODE=transaction
DB_DISABLE_PREPARED_STATEMENTS=true
```

Notes:

- Port `6543` is commonly used for Supabase transaction-mode pooler traffic.
- URL-encode any special characters in the password.
- Keep `postgresql+psycopg` as the SQLAlchemy driver prefix.
- Do not route Alembic migrations through the transaction pooler unless direct
  connectivity is impossible.

### Alembic, Backup, Restore, and Seed Jobs

Prefer direct PostgreSQL connections for schema changes and one-off operations:

```text
DATABASE_URL=postgresql+psycopg://postgres:<PASSWORD>@db.<PROJECT_REF>.supabase.co:5432/postgres
DB_POOLER_MODE=direct
DB_DISABLE_PREPARED_STATEMENTS=false
```

Direct connections give Alembic a stable backend session and avoid PgBouncer
transaction-mode limitations around session state.

## Prepared Statements and Transaction Pooling

Supabase transaction mode does not support session-scoped prepared statements
reliably because client transactions may be assigned to different backend
sessions. For psycopg 3, disable server-side prepared statements with:

```python
connect_args = {"prepare_threshold": None}
```

`backend/app/database.py` does this automatically when:

- `DB_POOLER_MODE=transaction`, or
- the URL uses port `6543`, or
- the host contains `pooler.supabase.com`, or
- `DB_DISABLE_PREPARED_STATEMENTS=true`.

If the project ever switches to `postgresql+asyncpg`, the equivalent setting is
driver-specific and should use:

```python
connect_args = {"prepared_statement_cache_size": 0}
```

Do not mix the two settings blindly. The current project uses psycopg, so
`prepare_threshold=None` is the correct production control.

## Zero-Downtime Alembic Strategy

Production migrations should be forward-compatible and split into safe phases.

### 1. Set Lock and Statement Timeouts

At the top of any risky migration, set short lock waits and bounded execution:

```python
from alembic import op


def upgrade() -> None:
    op.execute("SET lock_timeout = '5s';")
    op.execute("SET statement_timeout = '60s';")
```

This makes the migration fail fast instead of blocking live API traffic.
Retry during a lower-traffic window if it cannot acquire locks.

For very large backfills, prefer transaction-local settings inside explicit
chunks:

```python
op.execute("SET LOCAL lock_timeout = '5s';")
op.execute("SET LOCAL statement_timeout = '60s';")
```

### 2. Use Expand / Migrate / Contract

Do not deploy destructive schema changes in one step.

1. **Expand**: add nullable columns, new tables, new indexes, or compatibility
   views without removing old columns.
2. **Deploy app**: write both old and new formats when needed; read from the new
   column only when present.
3. **Backfill**: run small id-range batches outside request paths.
4. **Validate**: compare counts and null rates.
5. **Contract**: only after all workers run the new code, drop old columns or
   constraints.

### 3. Prefer Concurrent Indexes for Large Tables

For large tables, avoid blocking writes with a normal `CREATE INDEX`. Use:

```python
from alembic import op


def upgrade() -> None:
    op.execute("COMMIT")
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS
        ix_user_view_logs_user_created
        ON user_view_logs (user_id, created_at DESC);
        """
    )
```

`CREATE INDEX CONCURRENTLY` cannot run inside a normal transaction block, so
use this pattern only in a migration that is intentionally designed for it.
For pgvector HNSW indexes, schedule index creation during low traffic and test
on staging first.

### 4. Avoid Table Rewrites

Risky examples:

- adding a non-null column with a volatile default to a large table,
- changing a column type in place,
- adding a constraint with immediate validation,
- large `UPDATE` statements without batching.

Safer alternatives:

- add nullable column first,
- backfill in batches,
- add `CHECK (...) NOT VALID`,
- validate later with `VALIDATE CONSTRAINT`,
- only then set `NOT NULL` if needed.

### 5. Operational Runbook

Before migration:

```powershell
cd backend
$env:DATABASE_URL = "postgresql+psycopg://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres"
alembic heads
alembic current
alembic upgrade head --sql > migration-preview.sql
```

During release:

```powershell
alembic upgrade head
alembic current
```

After release:

- check `/health`,
- check Supabase database connections and pooler client graphs,
- verify p95/p99 API latency,
- verify Sentry and security alerts,
- keep the previous backend revision ready for rollback.

If a migration fails due to `lock_timeout`, do not increase the timeout first.
Inspect blocking sessions and retry during a lower-traffic window.

