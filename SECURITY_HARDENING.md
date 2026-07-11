# NCKUall Security Hardening

## Frontend Security Headers

`frontend/middleware.ts` adds security headers to every page response:

- `Content-Security-Policy`
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- `Cross-Origin-Opener-Policy: same-origin`
- `Cross-Origin-Resource-Policy: same-origin`

Production CSP is nonce-based and does not allow `unsafe-inline` scripts.
Development mode allows the minimum extra sources required by Next.js HMR.

Required frontend production env values:

```bash
NEXT_PUBLIC_SITE_URL=https://nckuall.example
NEXT_PUBLIC_API_BASE_URL=https://api.nckuall.example
NEXT_PUBLIC_WS_URL=wss://api.nckuall.example/ws/notifications
NEXT_PUBLIC_SUPABASE_URL=https://<project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon-key>
```

Nonce CSP requires per-request rendering in App Router. If a future route must
stay fully static/ISR, use a route-specific relaxed policy only for that route
or replace the inline script with a hashed/non-inline alternative.

## Backend CORS

FastAPI uses strict CORS helpers from `backend/app/security/cors.py`.

When `CORS_ALLOW_CREDENTIALS=true`, the app refuses to start if any of these
values contains `*`:

- `CORS_ORIGINS`
- `CORS_ALLOW_METHODS`
- `CORS_ALLOW_HEADERS`

Recommended production values:

```bash
CORS_ORIGINS=https://nckuall.example,https://nckuall.vercel.app
CORS_ALLOW_CREDENTIALS=true
CORS_ALLOW_METHODS=GET,POST,PUT,PATCH,DELETE,OPTIONS
CORS_ALLOW_HEADERS=Accept,Authorization,Content-Type,Origin,X-API-KEY,X-Request-ID,X-Requested-With
CORS_EXPOSE_HEADERS=Retry-After,X-RateLimit-Limit,X-RateLimit-Remaining,X-RateLimit-Reset,X-Request-ID
```

## Auth Cookies

Current API endpoints use Supabase Bearer JWTs. If the backend later sets
session cookies directly, use `backend/app/security/cookies.py` so cookies are
issued with:

- `HttpOnly=true`
- `Secure=true`
- `SameSite=lax` by default, or `strict` for same-site-only admin flows

Production values:

```bash
AUTH_COOKIE_HTTPONLY=true
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=lax
```

Use `SameSite=none` only when a cross-site embedded flow is unavoidable, and
only together with `Secure=true`.

## Error Redaction

`backend/app/security/exceptions.py` never returns raw stack traces, SQL
statements, or Pydantic internals to clients. All unexpected 5xx responses are
replaced with a generic message plus a safe support code:

```json
{
  "detail": "系統暫時無法處理請求，請稍後再試。",
  "error_code": "server_error",
  "safe_error_id": "NCKUALL-500-XXXXXXXX",
  "request_id": "..."
}
```

The real exception is still captured in server logs and Sentry with sanitized
request context for debugging.
