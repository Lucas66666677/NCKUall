# NCKUall Developer API

## Authentication model

`GET /api/courses` and `GET /api/events` remain available to guests because
NCKUall's Level 0 policy keeps all read routes public. External integrations
should send their issued credential in `X-API-KEY`; when the header is present,
the server requires a valid, active, unexpired key, checks its scope, and
applies a Redis-backed 60 requests-per-minute quota.

Available scopes:

- `courses:read`
- `events:read`

Only an HMAC-SHA256 digest is stored. The plaintext key is displayed once and
must be kept in a secret manager, never committed to a repository or bundled
into browser JavaScript.

## Provision a key

Apply the migration and configure the HMAC secret:

```powershell
cd backend
$env:DATABASE_URL = "postgresql+psycopg://..."
$env:API_KEY_HASH_SECRET = "at-least-32-random-characters"
alembic upgrade head
python scripts/create_developer_key.py `
  --owner "NCKU Student Association" `
  --owner-email "developer@example.org" `
  --scope courses:read `
  --scope events:read `
  --expires-in-days 365 `
  --environment live
```

Use the one-time value:

```bash
curl \
  -H "X-API-KEY: ncku_live_REDACTED" \
  "https://api.example.com/api/courses?limit=20"
```

Successful authenticated responses include:

- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `X-RateLimit-Reset`

Exhausted quotas return `429` with `Retry-After`. Invalid and expired keys
return `401`; missing scopes return `403`.

To revoke a compromised credential without deleting its audit record:

```sql
UPDATE developer_keys
SET is_active = false, revoked_at = now()
WHERE id = 'KEY_UUID';
```

## Generate a TypeScript/Axios SDK

Start FastAPI, then run:

```powershell
cd backend
.\scripts\generate_typescript_sdk.ps1 `
  -OpenApiUrl "http://127.0.0.1:8000/openapi.json"
```

The default output is `sdks/nckuall-typescript`. The generated client supports
the OpenAPI `DeveloperApiKey` security scheme. Configure the generated client
at runtime rather than writing the key into source code.

Equivalent cross-platform command:

```bash
curl -fsS http://127.0.0.1:8000/openapi.json -o openapi.json
npx --yes @openapitools/openapi-generator-cli generate \
  -i openapi.json \
  -g typescript-axios \
  -o ../sdks/nckuall-typescript \
  --additional-properties=npmName=@nckuall/api-client,npmVersion=1.0.0,supportsES6=true
```

For reproducible CI builds, pin the npm CLI package and generator version in
the consuming repository.

## Strict third-party enforcement

Because these endpoints intentionally remain public for students, an anonymous
caller can omit the key and use the guest API surface. If enforceable partner
quotas become mandatory, expose a versioned `/api/developer/v1/*` surface with
`auto_error=True`, or route all first-party reads through a trusted Next.js
backend before making API keys mandatory. Browser-side secrets cannot securely
identify first-party traffic.
