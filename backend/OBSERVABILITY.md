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

## Which revision is deployed

`GET /version` reports the commit the running process was built from:

```json
{ "revision": "c34a9e5b1d4f7a20e8c96b3d5f1a7e2c9b804d61" }
```

It is unauthenticated, like `/livez`, and answers without touching the
database, so it still responds during an outage. Three answers, and each means
something different:

| Response | What is deployed |
| --- | --- |
| `404` | A build older than the commit that added this route. Useful on its own: it means a merge has not reached the service. |
| `{"revision": null}` | This build or later, with `RENDER_GIT_COMMIT` unset or not a commit SHA. |
| `{"revision": "<sha>"}` | Exactly that commit. |

```bash
curl -fsS https://<api-host>/version
```

Compare the value with `git rev-parse origin/main` to tell a service running
current `main` from one still serving an earlier build.

`null` on a Render service is worth a second look. Render sets
`RENDER_GIT_COMMIT` itself, so `null` there means either the variable never
reached the process or something overrode it with a value that is not a commit
SHA. Only the second case is logged, once, at startup:

```text
RENDER_GIT_COMMIT is set but is not a commit SHA (length 15); the deployed
revision will be reported as unknown.
```

The value itself is never logged and never returned. `app/revision.py` accepts
7-40 hexadecimal characters and publishes nothing else, so a variable holding
a database URL, an API key or a pasted `.env` line reports `null` instead of
being echoed to an anonymous caller. `backend/tests/test_deployed_revision_contract.py`
drives exactly those values through the route.

The route is deliberately separate from `/livez` and `/health`. Both of those
payloads are contracts already: the container health gate parses `/livez`, and
`HA_DR_RUNBOOK.md` reads `/health`'s `read_only` and `degraded` states.

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
