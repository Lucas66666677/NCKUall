# NCKUall Production HA And Disaster Recovery Runbook

This runbook describes the production target architecture for high
availability, no single point of failure, and graceful degradation.

## 1. Database Read/Write Splitting

The FastAPI backend now creates four SQLAlchemy engines in
`backend/app/database.py`:

- `async_write_engine` / `write_engine`
  - connects to `DATABASE_URL`
  - must point to the Supabase primary writer
- `async_read_engine` / `read_engine`
  - connects to `DATABASE_READ_URL`
  - should point to an out-of-region read replica or read-only pooler

Request routing:

- `GET`, `HEAD`, `OPTIONS` use read sessions.
- `POST`, `PUT`, `PATCH`, `DELETE` use write sessions.
- Background analytics, audit logs, chat history, and notification writes keep
  using the writer via `AsyncWriteSessionLocal`.
- Agentic RAG tool retrieval uses `AsyncReadSessionLocal` so expensive
  semantic reads do not hit the primary writer.

Production env:

```bash
DATABASE_URL=postgresql+psycopg://postgres:<password>@<primary-host>:6543/postgres
DATABASE_READ_URL=postgresql+psycopg://postgres:<password>@<replica-host>:6543/postgres
DB_POOLER_MODE=transaction
DB_DISABLE_PREPARED_STATEMENTS=true
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=50
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=1800
DB_POOL_PRE_PING=true
READ_ONLY_GUARD_ENABLED=true
READ_ONLY_PROBE_INTERVAL_SECONDS=15
```

For Supabase/Supavisor transaction poolers, keep prepared statements disabled.
Transaction poolers can move a request across backend sessions, while prepared
statements are session-scoped.

## 2. Read-Only Failover Logic

`backend/app/availability.py` provides:

- `check_database_health(app)`
- `ReadOnlyModeMiddleware`

Behavior:

1. `/health` pings both writer and read replica.
2. If writer fails but read replica works, `app.state.read_only_mode=true`.
3. Unsafe methods are blocked before reaching route handlers.
4. Read routes continue serving through the read replica.
5. Responses include:

```json
{
  "detail": "系統目前處於唯讀模式，暫停新增、修改與刪除操作。",
  "error_code": "read_only_mode",
  "read_only": true
}
```

Frontend behavior:

- If any mutation receives `503` with `error_code=read_only_mode`, show:
  `系統目前進入唯讀保護模式，瀏覽功能正常，發布/修改稍後恢復。`
- Disable submit buttons for reviews, chat export jobs, admin mutations, and
  visual ingestion until `/health.read_only=false`.

Important: read-only mode is a graceful degradation, not automatic database
promotion. Promote a replica only after confirming replication lag and data
integrity.

## 3. Cloudflare Tunnel

Use Cloudflare Tunnel so the backend origin has no public inbound port. The
origin only opens outbound `cloudflared` connections to Cloudflare.

Recommended layout:

```text
Students
  -> Cloudflare DNS/WAF/CDN
  -> Cloudflare Tunnel
  -> Render/GCP private backend service
  -> Supabase primary / read replica
```

Dashboard setup:

1. Cloudflare Dashboard -> Zero Trust / Cloudflare One -> Networks -> Tunnels.
2. Create tunnel: `nckuall-api-prod`.
3. Install `cloudflared` on the backend host or sidecar container.
4. Route public hostname:
   - `api.nckuall.tw`
   - service: `http://127.0.0.1:8000`
5. Lock the origin firewall so only loopback/private network can reach FastAPI.

Container example:

```yaml
services:
  api:
    image: nckuall-api:latest
    expose:
      - "8000"
    env_file:
      - .env.production

  cloudflared:
    image: cloudflare/cloudflared:latest
    command: tunnel --no-autoupdate run --token ${CLOUDFLARE_TUNNEL_TOKEN}
    restart: unless-stopped
    depends_on:
      - api
```

## 4. Cloudflare WAF And DDoS Rules

Use WAF custom rules for deterministic blocks and managed challenges. Use rate
limiting rules for abusive but not necessarily malicious traffic.

Suggested WAF custom rules:

```text
Rule: Block known hostile networks
Expression:
  (ip.src in $nckuall_blocked_networks)
Action:
  Block
```

```text
Rule: Challenge non-browser API scanners
Expression:
  http.host eq "api.nckuall.tw"
  and http.request.uri.path starts_with "/api/"
  and not http.user_agent contains "Mozilla"
  and not http.request.headers["x-api-key"][0] ne ""
Action:
  Managed Challenge
```

```text
Rule: Protect admin
Expression:
  http.request.uri.path starts_with "/api/admin"
  and not ip.src in $trusted_admin_ips
Action:
  Managed Challenge or Block
```

Suggested rate limiting rules:

```text
POST /api/chat
Threshold: 30 requests / minute / IP
Action: Managed Challenge, then Block for repeated abuse
```

```text
POST /api/life/reviews, POST /api/courses/*/reviews
Threshold: 10 requests / 10 minutes / IP
Action: Managed Challenge
```

```text
POST /api/admin/*, POST /api/admin/ingest/visual
Threshold: 5 requests / minute / IP
Action: Block
```

Keep application-level Redis rate limits enabled. Cloudflare protects the edge;
FastAPI still needs user-aware limits based on JWT/API key identity.

## 5. Cloudflare Cache Rules For Next.js

If the frontend is on Vercel, do not blindly cache all HTML at Cloudflare.
Vercel already manages ISR and dynamic rendering. Use Cloudflare primarily for
security, DNS, TLS, WAF, and static asset acceleration.

Recommended Cache Rules:

```text
Rule: Cache immutable Next assets
Expression:
  http.request.uri.path starts_with "/_next/static/"
Action:
  Cache eligible content
  Edge TTL: 1 year
  Browser TTL: Respect origin
```

```text
Rule: Bypass private/auth routes
Expression:
  http.request.uri.path starts_with "/admin"
  or http.request.uri.path starts_with "/auth"
  or http.request.uri.path starts_with "/api/"
Action:
  Bypass cache
```

```text
Rule: Respect Vercel ISR headers
Expression:
  http.host eq "nckuall.tw"
Action:
  Respect origin Cache-Control
```

Avoid caching HTML pages that depend on cookies, Supabase auth, personalized
recommendations, or `x-nonce` CSP headers.

## 6. Disaster Recovery Runbook

### Writer outage, read replica healthy

1. `/health` reports:

```json
{
  "status": "read_only",
  "database_write_ok": false,
  "database_read_ok": true,
  "read_only": true
}
```

2. Confirm Supabase status and connection pool metrics.
3. Keep NCKUall in read-only mode.
4. Pause crawlers, visual ingestion, backup restore jobs, and admin mutations.
5. Notify students in frontend banner.
6. Restore writer or promote replica according to Supabase procedure.
7. After writer recovers, `/health` flips `read_only=false` within
   `READ_ONLY_PROBE_INTERVAL_SECONDS`.

### Read replica outage, writer healthy

Options:

1. Temporarily set `DATABASE_READ_URL=DATABASE_URL`.
2. Redeploy backend.
3. Monitor primary CPU/connection pool closely.

The app will not enter read-only mode if writer works but replica fails; this
is a degraded performance state, not a data safety event.

### Full database outage

1. `/health` reports `status=degraded`.
2. Cloudflare should keep static frontend shell online.
3. Backend writes and reads will fail with sanitized 5xx responses.
4. Restore from Supabase PITR or latest `backup_manager.py` archive.
5. Rebuild vector indexes:

```sql
REINDEX INDEX CONCURRENTLY ix_career_document_chunks_embedding_hnsw;
```

6. Run smoke tests:

```bash
GET /health
GET /api/departments
GET /api/courses
POST /api/chat
```

## 7. Monitoring And Alerts

Minimum alerts:

- `/health.status != ok` for 2 consecutive checks.
- `read_only=true` for more than 60 seconds.
- Supabase connection usage over 75%.
- primary write latency p95 over 500 ms.
- read replica lag over 30 seconds.
- Cloudflare WAF blocks over baseline.
- FastAPI `read_only_mode` response count > 0.

## References

- Cloudflare Tunnel docs: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/
- Cloudflare WAF custom rules: https://developers.cloudflare.com/waf/custom-rules/
- Cloudflare rate limiting rules: https://developers.cloudflare.com/waf/rate-limiting-rules/
