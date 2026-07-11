# Production environment variables

Use separate Supabase projects and credentials for preview/staging and
production. Never expose database passwords, JWT secrets, service-role keys, or
AI provider keys through a `NEXT_PUBLIC_*` variable.

## Vercel / Next.js

Configure these in **Vercel > Project > Settings > Environment Variables**.
Public variables are bundled into the browser, so redeploy after changing them.

- `NEXT_PUBLIC_SITE_URL` (required): Canonical public HTTPS origin used by
  OpenGraph, Twitter cards, robots.txt, and sitemap.xml. Example:
  `https://nckuall.example`.
- `NEXT_PUBLIC_API_BASE_URL` (required): Public HTTPS origin of the FastAPI
  service, without `/api` and preferably without a trailing slash. Example:
  `https://api.example.com`.
- `NEXT_PUBLIC_WS_URL` (required for realtime notifications): Public WebSocket
  endpoint of FastAPI. Use `wss://` in production, for example
  `wss://api.example.com/ws/notifications`.
- `NEXT_PUBLIC_SUPABASE_URL` (required): Supabase project URL from the API
  settings. Example: `https://project-ref.supabase.co`.
- `NEXT_PUBLIC_SUPABASE_ANON_KEY` (required): Supabase publishable/anonymous
  key. This key is designed for browser use; authorization must still be
  enforced by RLS and the FastAPI backend.
- `NEXT_PUBLIC_DEFAULT_DEPARTMENT_ID` (recommended): UUID of the seeded default
  department, currently intended to be the `DPS` row.
- `API_BASE_URL` (recommended, server-only): FastAPI origin used by dynamic
  metadata and server-rendered course detail pages. It may match
  `NEXT_PUBLIC_API_BASE_URL`, but must not include `/api`.

The application currently reads `NEXT_PUBLIC_API_BASE_URL`; do not configure
only `NEXT_PUBLIC_API_URL`.

In Supabase Auth, add the production Vercel domain to the allowed redirect URLs,
including:

```text
https://your-domain.example/auth/callback
```

## Render or GCP / FastAPI

Configure these only in the backend service's secret/environment settings.

- `DATABASE_URL` (required): Supabase PostgreSQL URL using SQLAlchemy's psycopg
  3 prefix, for example
  `postgresql+psycopg://USER:PASSWORD@HOST:5432/postgres`. URL-encode special
  characters in the password. Prefer direct port `5432` for migrations,
  backups, restores, and seed jobs. For the long-running FastAPI web service
  under high concurrency, use the Supabase pooler transaction endpoint when
  needed, commonly:
  `postgresql+psycopg://postgres.<PROJECT_REF>:<PASSWORD>@aws-0-<REGION>.pooler.supabase.com:6543/postgres`.
- `DB_POOL_SIZE` (recommended): Defaults to `20` per API worker.
- `DB_MAX_OVERFLOW` (recommended): Defaults to `50` burst connections per API
  worker.
- `DB_POOL_TIMEOUT` (recommended): Defaults to `30` seconds.
- `DB_POOL_RECYCLE` (recommended): Defaults to `1800` seconds.
- `DB_POOL_PRE_PING` (recommended): Keep `true` to detect stale connections.
- `DB_POOL_USE_LIFO` (recommended): Keep `true` to let idle connections age out
  more naturally during off-peak windows.
- `DB_POOLER_MODE` (hosting-specific): Use `transaction` for Supabase pooler
  port `6543`; use `direct` for direct Postgres or migration jobs.
- `DB_DISABLE_PREPARED_STATEMENTS` (pooler-specific): Leave unset for
  auto-detection, or set `true` when using transaction pooling. With the
  current `psycopg` driver this sets `prepare_threshold=None`.
- `DB_APPLICATION_NAME` (recommended): Defaults to `nckuall-api`; visible in
  PostgreSQL activity views.
- `SERVICE_NAME` (recommended): Stable JSON log service label, for example
  `nckuall-api`.
- `APP_ENV` (required): `production`, `staging`, or `development`.
- `LOG_LEVEL` (recommended): Usually `INFO` in production.
- `SENTRY_DSN` (required for monitoring): Backend Sentry project DSN. Keep it
  in the backend service environment.
- `SENTRY_ENVIRONMENT` (recommended): Match the deployment environment.
- `SENTRY_RELEASE` (recommended): Git SHA or release identifier used to group
  errors by deployment.
- `SENTRY_ERROR_SAMPLE_RATE` (recommended): `1.0` captures every error event.
- `SENTRY_TRACES_SAMPLE_RATE` (cost control): Start around `0.1` and tune based
  on Sentry event volume.
- `CORS_ORIGINS` (required): Comma-separated frontend origins, with no path.
  Example: `https://ncku.example,https://ncku-project.vercel.app`. Include
  explicit preview domains only when they should be trusted.
- `CHECK_VECTOR_INDEXES_ON_STARTUP` (recommended): Set to `true` to run a
  read-only HNSW index health check when each API worker starts. Index creation
  remains an Alembic release step.
- `REDIS_URL` (required for chat and multi-worker realtime notifications): TLS
  Redis URL from Render, Memorystore, or another production Redis provider.
  The activity crawler must use the same Redis instance. Use `rediss://` when
  the provider requires TLS.
- `API_KEY_HASH_SECRET` (required for developer API keys): Stable random secret
  of at least 32 characters used as the HMAC pepper. Rotating it invalidates
  every issued developer key.
- `API_KEY_RATE_LIMIT_ENABLED` (required): Keep `true` in production.
- `API_KEY_RATE_LIMIT_PER_MINUTE` (recommended): Defaults to `60`.
- `API_KEY_RATE_LIMIT_FAIL_OPEN` (recommended): Keep `false` so authenticated
  third-party traffic is rejected when Redis cannot enforce its quota.
- `BACKUP_HMAC_SECRET` (required for disaster recovery): Stable random secret
  of at least 32 characters used to sign and authenticate backup manifests.
  Keep previous values available when rotating it so historical archives
  remain restorable.
- `NOTIFICATION_POPULAR_REVIEW_MIN_REPORTS` (recommended): Minimum report count
  required before approving a review emits a realtime notification. Default
  is `1`.
- `CHAT_RATE_LIMIT_ENABLED` (required): Keep `true` in production.
- `TRUST_PROXY_HEADERS` (hosting-specific): Set `true` only when the trusted
  ingress overwrites `X-Forwarded-For`; otherwise clients could spoof their IP.
- `RATE_LIMIT_FAIL_OPEN` (recommended): Keep `false` so a Redis outage cannot
  create unlimited LLM spending.
- `CHAT_MODERATION_ENABLED` (required): Keep `true` in production.
- `MODERATION_FAIL_OPEN` (recommended): Keep `false`.
- `MODERATION_OPENAI_API_KEY` (recommended): Dedicated OpenAI key for input
  moderation. When omitted, the backend falls back to `OPENAI_API_KEY`.
- `OPENAI_MODERATION_MODEL` (optional): Defaults to
  `omni-moderation-latest`.
- `MODERATION_TIMEOUT_SECONDS` (optional): Defaults to `5`.
- `SUPABASE_JWT_SECRET` (required by the current HS256 verifier): JWT secret
  from the same Supabase project used by the frontend. This is not the anon key
  and must never be exposed to Vercel browser variables.
- `SUPABASE_JWT_AUDIENCE` (recommended): `authenticated`.
- `RAG_EMBEDDING_PROVIDER` (required): `openai` or `google`.
- `RAG_CHAT_PROVIDER` (required): `openai` or `google`.
- `RAG_TEMPERATURE` (optional): Defaults to `0`.
- `OPENAI_API_KEY` (required when either provider is OpenAI).
- `OPENAI_EMBEDDING_MODEL` (optional): Defaults to
  `text-embedding-3-small`. Its output dimensions must match the database
  `vector(1536)` columns.
- `OPENAI_EMBEDDING_DIMENSIONS` (recommended): `1536`.
- `OPENAI_CHAT_MODEL` (optional): Defaults to `gpt-4o-mini`.
- `GOOGLE_API_KEY` (required when either provider is Google).
- `GOOGLE_EMBEDDING_MODEL` (optional): Defaults to
  `models/gemini-embedding-001`.
- `GOOGLE_EMBEDDING_DIMENSIONS` (required with Google): `1536`, matching the
  database schema.
- `GOOGLE_CHAT_MODEL` (optional): Defaults to `gemini-1.5-flash`.
- `PORT` (platform-provided): Render/GCP normally injects this; the Docker
  command already binds Gunicorn to it.
- `WEB_CONCURRENCY` and `GUNICORN_TIMEOUT` (optional): Docker defaults are `2`
  workers and `120` seconds.

Provider/model names should be reviewed before deployment because vendors may
retire model versions. Do not put both production and preview services against
the same database unless that is intentional.

## Release order

From the `backend/` directory, use the production `DATABASE_URL`:

```powershell
alembic upgrade head
python scripts/seed_departments.py --dry-run
python scripts/seed_departments.py
```

Then retrieve the default department UUID for Vercel:

```sql
SELECT id, code, name_zh
FROM departments
WHERE code = 'DPS';
```

Deploy the backend before the frontend so `NEXT_PUBLIC_API_BASE_URL` always
points to a healthy API. Validate `/health`, `/docs`, a public GET route,
Supabase Google login, an NCKU-authorized review POST, and `/api/chat` after
deployment.

For migration lock safety and pgBouncer transaction-mode caveats, see
`backend/DATABASE_OPERATIONS.md`.
