# Production migration checklist

**This file does not authorize a migration.** It is the procedure to follow
once someone with production database credentials has decided to run one.
Every step below is written to be *fail-closed*: it states the exact command,
the exact output that lets you continue, and the fact that **any other output
is a STOP**. A step with no stated expected output is not a check.

`backend/ALEMBIC.md` explains how Alembic is configured here and how to run it
generally. This file is narrower: it is the production run, and the
verification that the deployed application can actually use what the run
created.

---

## 0. Why this is a separate, manual action

The backend image does not migrate. `backend/Dockerfile` ends at

```dockerfile
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app.main:app"]
```

There is no `alembic upgrade head` in the image, no entrypoint script that runs
one, and no `render.yaml` in this repository that would run one on deploy. The
hosted backend auto-deploys from `main`.

The consequence is the thing this checklist exists for: **merging a migration
ships the code that needs the new tables, and does not create them.** The
schema and the code that reads it move on two independent timelines, and only
one of them is automatic.

Two revisions are in that position as of this file being written. Both are on
`main`, both are served by the deployed application, and whether either has
been applied to production is not knowable from this repository — step 1 is
what determines it:

| Revision | Creates | Reached by |
| --- | --- | --- |
| `20260826_0014` | `life_review_flags` | `POST /api/life/reviews/{review_id}/flag`, `GET /api/admin/reviews/flagged` |
| `20260827_0015` | `course_visual_submissions`, and the `course_submission_status` enum type | `POST /api/admin/ingest/visual` (course uploads), `GET /api/admin/course-submissions`, `POST /api/admin/course-submissions/{submission_id}/review` |

If the tables are absent, those routes still answer — authentication and
validation run first — and fail inside the request with a database error, not
at startup and not on `/health`. Neither `/livez` nor `/health` reads either
table, so **a fully green health check is consistent with both tables missing.**
Do not use either endpoint as evidence about schema state.

---

## 1. Determine the current state before changing anything

Owner-gated input required before this step: a **direct** (non-pooler)
production `DATABASE_URL` for a role that can `CREATE TABLE`, `CREATE INDEX`
and `CREATE TYPE` in the target schema. Nothing below should be attempted
through a connection pooler — see `backend/ALEMBIC.md`.

```bash
cd backend
alembic current
```

Record the output verbatim in the change record. It is the only thing that
tells you what a rollback has to return to.

- Output is `20260827_0015 (head)` → the chain is already applied. **Skip to
  step 5.** Do not run an upgrade "to be sure".
- Output is `20260708_0013` (or any earlier revision) → continue to step 2.
- Output is empty, or `alembic current` errors → **STOP.** An empty result means
  either an unmigrated database or a missing `alembic_version` table, and those
  need opposite responses. Resolve which one it is before going further;
  `backend/ALEMBIC.md` covers the `create_all`-then-stamp case.

---

## 2. Confirm the chain is unambiguous

```bash
alembic heads
```

- Exactly one line, `20260827_0015 (head)` → continue.
- Two or more heads → **STOP.** `alembic upgrade head` is ambiguous with a
  branched chain and there is no correct guess. `backend/tests/test_migration_gate_contract.py`
  fails on this in CI, so reaching production with two heads means the gate was
  bypassed.

---

## 3. Back up, and prove the backup exists

Take a full backup (Supabase dashboard, or `pg_dump` against the direct URL)
and record its identifier and timestamp.

- A backup whose completion you have **confirmed in the provider's own UI or in
  `pg_dump`'s exit status** → continue.
- A backup you requested and did not confirm → **STOP.** An unverified backup is
  not a rollback plan, and step 6's rollback for `20260827_0015` is not
  loss-free without one.

---

## 4. Rehearse, then apply

Rehearse on a restored copy of the backup, never on production first:

```bash
alembic upgrade head     # against the RESTORED COPY
alembic current
```

- `20260827_0015 (head)`, no errors → continue to the production run.
- Anything else → **STOP.** Whatever failed on the copy will fail on production.

Then, against production:

```bash
alembic upgrade head
alembic current
```

- `20260827_0015 (head)` → continue to step 5.
- Any error → **STOP** and go to step 6. Do not re-run `upgrade head` after a
  partial failure without first reading `alembic current`; Alembic runs each
  revision in a transaction, but `CREATE TYPE course_submission_status` in
  `20260827_0015` is created with `checkfirst=True` precisely because a retry
  can otherwise collide with a type left behind.

---

## 5. Prove the schema is what the application expects

`alembic current` says a revision ran. It does not say the objects exist —
a hand-applied schema plus a `stamp` produces the same output. Check the
objects directly:

```sql
SELECT to_regclass('public.life_review_flags'),
       to_regclass('public.course_visual_submissions');
```

- Both non-null → continue.
- Either is null → **STOP.** The version table and the schema disagree; this is
  the `stamp`-without-`upgrade` state, and it is worse than an unmigrated
  database because every automated check now reports success.

```sql
SELECT 1 FROM pg_type WHERE typname = 'course_submission_status';
```

- One row → continue.
- No rows → **STOP.** `course_visual_submissions.status` cannot be written.

```sql
SELECT indexname FROM pg_indexes
 WHERE tablename IN ('life_review_flags', 'course_visual_submissions')
 ORDER BY indexname;
```

Expect exactly these seven, and treat a missing one as a STOP — they are what
the review queue and the duplicate-upload check read:

```
ix_course_visual_submissions_course_id
ix_course_visual_submissions_status_created
ix_course_visual_submissions_submitted_by_user_id
ix_course_visual_submissions_upload_sha256
ix_life_review_flags_life_review_id
ix_life_review_flags_reporter_user_id
ix_life_review_flags_review_created
```

---

## 6. Authenticated course-ingest verification

Only after step 5 passes. This is the check that the *deployed* application can
use the schema — not the local one, and not the test database, which
`backend/tests/conftest.py` builds with `Base.metadata.create_all` and which
therefore proves nothing about the migration.

Owner-gated input required: a bearer token for an account the ingest route
accepts. `verify_visual_ingestion_user` in `backend/app/auth.py` admits an
administrator **or** any verified NCKU-domain account; nothing else. Obtaining
that token is the account owner's action — it is not something this checklist,
or any automated agent, can produce.

Run these against the deployed backend, in order, and stop at the first
mismatch.

**6a. The route rejects an anonymous caller.** Establishes that a later success
was actually authenticated:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  https://nckuall.onrender.com/api/admin/ingest/visual
```

- `401` → continue.
- `200`, or any 2xx → **STOP.** The route is unauthenticated in production.
- `403` → the request carried a rejected identity; re-run with no credentials
  at all before drawing a conclusion.

**6b. A real course upload is accepted and persisted.** Use a document you are
willing to have stored — the upload's SHA-256 is retained on the row.

```bash
curl -s -X POST https://nckuall.onrender.com/api/admin/ingest/visual \
  -H "Authorization: Bearer $TOKEN" \
  -F 'ingest_type=course' \
  -F 'file=@<path-to-course-document>.pdf'
```

- `200` with a JSON body carrying `action` and `resource_id` → continue.
- `500`, or any database error → **STOP.** This is what an unapplied
  `20260827_0015` looks like from the outside: a route that authenticates you
  and then fails on the write. Return to step 1.
- `403` → the token's account is neither an administrator nor NCKU-verified.
  This is an account problem, not a schema problem; the migration is unaffected.
- `429` → the per-user rate limit in `app/security/visual_ingestion.py`. Wait
  and repeat; it is not a failure of this check.

**6c. The submission is visible to the review queue.** A non-admin upload is
queued rather than applied, so this is the step that proves the row was
actually written and is readable through the enum-ordered index:

```bash
curl -s https://nckuall.onrender.com/api/admin/course-submissions \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

- `200`, and the `resource_id` from 6b appears in the body → **the ingest path
  is verified end to end.**
- `200` with the submission absent → **STOP.** The write and the read disagree;
  do not close the verification.
- `500` → **STOP.** Return to step 1.

Record the response of 6a, 6b and 6c in the change record. A migration verified
only by `alembic current` is not verified.

---

## 7. Rollback

Rollback is per-revision and is **not** loss-free. Read this before step 4, not
after it fails.

```bash
alembic downgrade 20260826_0014   # undoes 20260827_0015
alembic downgrade 20260708_0013   # undoes 20260826_0014
```

- `20260827_0015`'s downgrade drops `course_visual_submissions` **and** the
  `course_submission_status` type. Every queued and reviewed submission is
  destroyed.
- `20260826_0014`'s downgrade drops `life_review_flags`. Every report on a life
  review is destroyed, and reports are how a review gets hidden — dropping the
  table un-hides everything that had been reported.

So: if either table has taken production writes, **restore the step 3 backup
instead of downgrading.** Downgrade is the right tool only in the window
between the upgrade and the first real write, which in practice means during
step 4 and step 5.

Rolling the *code* back does not require any of this. The application does not
read either table on a path that runs at startup, so an image built before
`20260826_0014` runs unchanged against a migrated database.

---

## What this checklist deliberately does not do

- It does not authorize the migration, and it is not a substitute for whatever
  approval the account owner requires.
- It does not create, seed or authenticate any account. Step 6 consumes a token;
  it does not produce one.
- It does not add an `alembic upgrade head` to the image or to a deploy hook.
  Making the migration automatic is a real design decision with its own failure
  mode — every replica racing the same upgrade on boot — and it belongs in its
  own change, not in a checklist.
