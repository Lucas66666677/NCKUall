# Karma Engine And Anti-Fraud Reviews

## Data Model

The platform keeps reviews public-anonymous while still maintaining a private
trust score:

- `users`
  - `id`: Supabase `sub` when available, otherwise a stable hash fallback.
  - `email_hash`: SHA-256 hash only. Plain email is not stored here.
  - `karma_points`: ranking and trust score.
- `life_reviews` / `course_reviews`
  - `author_user_id`
  - `is_approved`
  - `score`
  - `ai_spam_confidence`
- `life_review_votes`
  - one upvote per verified user per life review.

## Karma Rules

- Approved authentic review: `+10`
- Upvote from another NCKU-verified user: `+2`
- Admin-confirmed harmful/flagged review hidden: `-20`

Karma is not exposed as identity. It is used to weight default review ranking:

```text
review_weight = review.score + author.karma_points * 0.01
```

## Anti-Fraud Flow

When `POST /api/life/reviews` is called:

1. The backend creates or loads the caller's private user profile.
2. It counts reviews from the same user in the previous hour.
3. It compares the submitted title/content against existing relevant reviews
   using Levenshtein similarity.
4. If similarity is `>= 0.85`, the request is rejected with `HTTP 400`.
5. If the user already posted 3 reviews in the last hour, the new review is
   stored as `PENDING` and `is_approved=false`.
6. Otherwise, the review is auto-approved and the author receives `+10` Karma.

## Moderation

`POST /api/life/reviews/{review_id}/flag` moves a review to `PENDING`. No Karma
penalty is applied at this stage.

When an admin sets the review status to `HIDDEN`, the author receives `-20`
only if the review had reports.

When an admin approves a pending review, the author receives `+10`.

Run migrations before deployment:

```bash
cd backend
alembic upgrade head
```
