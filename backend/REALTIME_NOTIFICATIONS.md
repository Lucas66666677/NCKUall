# Realtime notifications

## Architecture

Browser clients connect to `GET /ws/notifications`. Each FastAPI worker owns
its local WebSocket connections, while Redis Pub/Sub fans one notification out
to every worker. This avoids relying on sticky sessions and works with multiple
Gunicorn containers.

Topic rules:

- Every connection subscribes to `all`.
- A selected department adds `department:{department_id}`.
- Campus-wide events use `topic="all"`.
- Department-only notices use `topic="department:{department_id}"`.

Redis Pub/Sub is intentionally ephemeral: students who are offline do not
receive old notifications. Add a persisted notification inbox if replay or
delivery guarantees become a product requirement.

## Environment

Backend:

```dotenv
REDIS_URL=redis://localhost:6379/0
CORS_ORIGINS=http://localhost:3000,https://nckuall.example
NOTIFICATION_POPULAR_REVIEW_MIN_REPORTS=1
```

Frontend:

```dotenv
NEXT_PUBLIC_WS_URL=ws://127.0.0.1:8000/ws/notifications
```

Production must use `wss://` when the website is served over HTTPS. The
activity crawler must receive the same `REDIS_URL` as FastAPI so newly inserted
events can reach every API worker.

## Event producers

- Admin moderation publishes `review.approved` in a FastAPI background task
  only when a reported review transitions into `APPROVED`.
- `scripts/activity_event_pipeline.py` publishes `event.created` only for new
  rows after the database transaction commits.

Example department notification:

```python
await notification_broker.publish(
    NotificationPayload(
        kind="review.approved",
        topic=f"department:{department_id}",
        title="光電系新增熱門評論",
        summary="一筆新評論已通過管理員審核。",
        href=f"/life#review-{review_id}",
        resource_id=str(review_id),
    )
)
```

## Operational notes

- Keep Redis private and require TLS/authentication in production.
- Load balancers must support WebSocket upgrade and use an idle timeout longer
  than the 25-second browser heartbeat.
- The server drops connections that cannot accept a notification within two
  seconds, preventing one slow client from delaying the full broadcast.
- `CORS_ORIGINS` is also the WebSocket Origin allowlist.
