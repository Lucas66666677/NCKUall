# NCKUall Production Compose Runbook

This compose stack runs the FastAPI backend and Redis cache on a VPS. The
frontend stays on Vercel.

Redis uses the official `redis:alpine` image. For fully reproducible releases,
pin it to a concrete tag such as `redis:7.4-alpine` after staging verification.

## 1. Prepare environment

```bash
cp backend/.env.production.example backend/.env.production
nano backend/.env.production
chmod 600 backend/.env.production
```

Fill every secret in `backend/.env.production`, especially:

- `DATABASE_URL`
- `DATABASE_READ_URL`
- `REDIS_PASSWORD`
- `SUPABASE_JWT_SECRET`
- `OPENAI_API_KEY` or `GOOGLE_API_KEY`
- `CORS_ORIGINS`
- `FRONTEND_REVALIDATE_URL`
- `REVALIDATION_SECRET`

For Supabase/Supavisor transaction pooling, keep:

```env
DB_POOLER_MODE=transaction
DB_DISABLE_PREPARED_STATEMENTS=true
```

## 2. Deploy

```bash
docker compose --env-file backend/.env.production -f docker-compose.prod.yml pull redis
docker compose --env-file backend/.env.production -f docker-compose.prod.yml build backend
docker compose --env-file backend/.env.production -f docker-compose.prod.yml up -d
```

The backend waits for Redis healthcheck, then runs:

```bash
alembic upgrade head
gunicorn --config gunicorn.conf.py app.main:app
```

## 3. Check health and logs

```bash
docker compose --env-file backend/.env.production -f docker-compose.prod.yml ps
curl -fsS http://127.0.0.1:8000/health
docker compose --env-file backend/.env.production -f docker-compose.prod.yml logs -f backend
docker compose --env-file backend/.env.production -f docker-compose.prod.yml logs -f redis
```

## 4. Update release

```bash
git pull
docker compose --env-file backend/.env.production -f docker-compose.prod.yml build backend
docker compose --env-file backend/.env.production -f docker-compose.prod.yml up -d backend
docker compose --env-file backend/.env.production -f docker-compose.prod.yml logs -f backend
```

## 5. Redis persistence

Redis runs with both AOF and RDB snapshots:

- AOF: `appendonly yes`, `appendfsync everysec`
- RDB: `save 900 1`, `save 300 10`, `save 60 10000`
- Data volume: `redis-data:/data`

Do not expose Redis publicly. It is only attached to the internal compose
network and requires `REDIS_PASSWORD`.
