# Alembic database migrations

Run every command in the `backend/` directory so `app` and `alembic.ini` can
be resolved correctly.

## 1. Install dependencies

```powershell
cd backend
python -m pip install -r requirements.txt
```

Alembic has already been initialized in this repository. For a brand-new
project, the equivalent initialization command is:

```powershell
alembic init -t async alembic
```

Do not run that initialization command again here because it would overwrite
the checked-in async configuration and revision template.

## 2. Configure the database

Set a SQLAlchemy async-compatible psycopg URL. Escape or URL-encode special
characters in the password.

```powershell
$env:DATABASE_URL = "postgresql+psycopg://postgres:<password>@<host>:5432/postgres"
```

For Supabase, prefer its direct database connection while running migrations.
If the environment only permits a pooler, use the pooler's documented host and
port while retaining the `postgresql+psycopg://` SQLAlchemy driver prefix.

The database role must be able to run:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Supabase normally has pgvector available, but the extension still needs to be
enabled in the target database.

## 3. Apply the initial migration

For an empty database:

```powershell
alembic upgrade head
alembic current
```

The initial revision enables pgvector before creating any `vector(1536)`
columns.

If the same schema was already created using `Base.metadata.create_all()`, do
not run the initial migration over those tables. First compare the live schema
with the migration, then mark that existing database as current:

```powershell
alembic stamp head
```

`stamp` records the revision without executing DDL, so use it only after
confirming that the existing schema matches.

## 4. Create later revisions

After changing `app/models.py`:

```powershell
alembic revision --autogenerate -m "describe the schema change"
```

Always review the generated file before applying it. In particular, verify:

- Destructive column or table changes are intentional.
- Enum changes use explicit PostgreSQL `ALTER TYPE` operations where needed.
- Renames were not generated as an unrelated drop and add.
- pgvector columns render as `pgvector.sqlalchemy.Vector(dim=1536)`.
- Data migrations occur before new `NOT NULL` constraints when required.

Then apply and inspect:

```powershell
alembic upgrade head
alembic current
```

Useful checks:

```powershell
alembic heads
alembic history --verbose
alembic check
alembic upgrade head --sql
```

Rollback one revision during development:

```powershell
alembic downgrade -1
```

Database migrations should run as a separate deployment/release step, not once
per Gunicorn worker during application startup.
