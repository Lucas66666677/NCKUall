# Backend observability

## JSON logs

All application, Uvicorn, and Gunicorn logs are emitted as one JSON object per
line to stdout. Render and GCP can ingest stdout directly.

Every HTTP completion log includes:

- `timestamp`, `severity`, `service`, and `environment`
- `request_id`, `method`, and `path`
- `status_code` and `duration_ms`
- `async_function` (or `sync:<function>` for synchronous endpoints)

Send an optional `X-Request-ID` using 1-64 letters, digits, dots, underscores,
or hyphens. Invalid values are replaced. The response always returns the final
ID, which can be searched in logs and Sentry.

Do not log request bodies, authorization headers, cookies, JWTs, database URLs,
or AI prompts. The formatter masks common credential patterns as defense in
depth.

## Sentry

Create a Sentry Python/FastAPI project and configure:

```env
SENTRY_DSN=https://...
SENTRY_ENVIRONMENT=production
SENTRY_RELEASE=<git-sha-or-release>
SENTRY_ERROR_SAMPLE_RATE=1.0
SENTRY_TRACES_SAMPLE_RATE=0.1
```

Error events are captured at 100%. Start performance tracing at 10%, then tune
it against traffic and Sentry quota. Request bodies and default PII are
disabled. A final `before_send` scrub removes password, token, authorization,
cookie, DSN, database URL, and Redis URL fields and common credential strings.

The global FastAPI 500 handler explicitly calls `capture_exception`, attaches
only request ID, method, and path, then returns a sanitized JSON response.

After deployment, trigger a controlled exception in a staging-only route or
test job. Confirm that:

1. The client sees a generic 500 with `request_id`.
2. stdout contains an ERROR request completion with the same ID.
3. Sentry receives the exception and matching request ID.
4. No authorization, cookie, request body, or database credential is present.
