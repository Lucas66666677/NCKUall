# NCKUall disaster recovery

`scripts/backup_manager.py` creates a signed, compressed snapshot of every
SQLAlchemy application table in the active PostgreSQL schema. It does not copy
Supabase-managed `auth`, `storage`, or other schemas.

## Required configuration

```dotenv
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/postgres
BACKUP_HMAC_SECRET=at-least-32-random-characters
```

Keep `BACKUP_HMAC_SECRET` in a secret manager and retain old versions when
rotating it. A backup cannot be authenticated or restored without the exact
secret used to create it.

The archive is compressed and signed, but not encrypted. Store it in encrypted
object storage with private access, versioning, retention, and a separate
account or project from the production database. Never commit archives to Git.

## Create a backup

Run migrations first, then:

```powershell
cd backend
python scripts/backup_manager.py --backup --output-dir D:\NCKUall-backups
```

The script:

1. Opens a PostgreSQL `REPEATABLE READ, READ ONLY` transaction.
2. Streams every table in primary-key order without loading the database into
   memory.
3. Serializes pgvector values as finite JSON float arrays.
4. Writes one NDJSON file per table.
5. signs the canonical manifest with HMAC-SHA256 and records a SHA-256 digest,
   byte size, and row count for every table.
6. Atomically renames the completed `.tar.gz`; partial archives are removed.

## Restore

Stop application writers or place the API in maintenance mode, then migrate an
empty or existing target database to the exact Alembic revision recorded in
the backup:

```powershell
cd backend
python scripts/backup_manager.py `
  --restore D:\NCKUall-backups\nckuall-backup-20260705T120000Z-ab12cd34.tar.gz
```

Before touching the database, the script validates:

- archive member paths and decompressed-size limits;
- manifest HMAC;
- per-table SHA-256, byte size, JSON format, row count, and columns;
- the current SQLAlchemy schema fingerprint;
- the target PostgreSQL schema, complete table set, Alembic revision, and HNSW
  index set.

Restore uses one `SERIALIZABLE` transaction. It takes a PostgreSQL advisory
lock, truncates all application tables together, restores them in foreign-key
order, and runs regular `REINDEX INDEX` for every HNSW index. Any insert,
constraint, lock, or reindex failure rolls the transaction back, including the
transactional `TRUNCATE`.

Regular `REINDEX` takes write-blocking locks. Run restores during a maintenance
window. The database role also needs ownership or PostgreSQL `MAINTAIN`
privileges for the indexed tables.

## Recovery drill

At least monthly:

1. Restore the newest archive into a separate Supabase staging project.
2. Compare table row counts and execute representative course and RAG queries.
3. Confirm all HNSW indexes are valid in `pg_index`.
4. Record restore duration and update the recovery time objective.
5. Delete the staging database only after the drill is documented.

Backups are not proven until a restore drill succeeds.
