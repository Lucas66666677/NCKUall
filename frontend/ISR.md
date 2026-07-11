# NCKUall ISR and on-demand revalidation

## Cache flow

### Departments and courses

1. `app/layout.tsx` fetches departments on the server and injects them into
   `AppProvider`. A visitor no longer makes a separate department request on
   first paint.
2. `app/courses/page.tsx` is an ISR Server Component with
   `revalidate = 86400`. It renders the default department's cached courses
   into the initial HTML/RSC payload.
3. Server fetches in `lib/server-data.ts` use a 24-hour Data Cache TTL and
   cache tags.
4. A client-side department switch calls the same-origin
   `GET /api/courses`. The Route Handler reads the same tagged Data Cache, so
   repeated users do not repeatedly query FastAPI or Supabase.

### Life reviews

1. `/life` receives its initial review list from an ISR Server Component.
2. Category changes call the same-origin `GET /api/life/reviews`, backed by
   the `life-reviews` Data Cache tag.
3. The student who creates a review sees the returned record immediately
   through an optimistic local insertion.
4. After the database commit, FastAPI schedules a background webhook to
   `POST /api/revalidate/life`.
5. The Route Handler authenticates the webhook, calls
   `revalidateTag("life-reviews")`, and calls `revalidatePath("/life")`.
   The next request regenerates the stale page/data and updates Vercel's
   durable ISR cache.

The 24-hour TTL remains a safety net if the webhook temporarily fails.

## Environment variables

### Vercel frontend

```dotenv
NEXT_PUBLIC_SITE_URL=https://nckuall.example
NEXT_PUBLIC_API_BASE_URL=https://api.nckuall.example
API_BASE_URL=https://api.nckuall.example
NEXT_PUBLIC_DEFAULT_DEPARTMENT_ID=<photonics-department-uuid>
REVALIDATION_SECRET=<at-least-32-random-characters>
```

`API_BASE_URL` and `REVALIDATION_SECRET` are server-only. Never prefix the
secret with `NEXT_PUBLIC_`.

### FastAPI production service

```dotenv
FRONTEND_REVALIDATE_URL=https://nckuall.example/api/revalidate/life
REVALIDATION_SECRET=<the-exact-same-secret-as-vercel>
```

The webhook URL must target the deployment/domain whose ISR cache should be
invalidated. A Preview Deployment webhook does not invalidate Production.

Generate a secret, for example:

```bash
openssl rand -hex 32
```

## Operational behavior

- The first successful build/request generates the static representation.
- Requests inside the 24-hour window are served from Vercel's CDN/ISR cache.
- After the TTL, stale content can be served while one background request
  refreshes the cache; concurrent traffic is collapsed.
- If regeneration fails, Vercel keeps serving the last successful version.
- On-demand revalidation marks only `/life` and the `life-reviews` data tag
  stale, avoiding a full deployment or whole-site purge.

ISR removes repeated origin/database reads, but it cannot literally guarantee
zero milliseconds: network latency, CDN location, hydration, and device render
time still exist. The practical target is a CDN cache hit with no Supabase read
on the request path.

## Manual webhook check

```bash
curl -X POST "https://nckuall.example/api/revalidate/life" \
  -H "Authorization: Bearer $REVALIDATION_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"event":"life.review.created","review_id":"test-review-id"}'
```

An invalid or missing secret returns `401`; an unsupported event returns
`400`; a frontend without `REVALIDATION_SECRET` returns `503`.
