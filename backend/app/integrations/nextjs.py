import logging
from os import getenv
from uuid import UUID

import httpx


logger = logging.getLogger(__name__)


async def revalidate_life_page(review_id: UUID | str) -> None:
    """Invalidate the cached Next.js life page without delaying the API response."""

    webhook_url = getenv("FRONTEND_REVALIDATE_URL", "").strip()
    secret = getenv("REVALIDATION_SECRET", "").strip()
    if not webhook_url or not secret:
        logger.info(
            "nextjs_revalidation_skipped",
            extra={"path": "/life", "reason": "not_configured"},
        )
        return

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(5.0),
            follow_redirects=False,
        ) as client:
            response = await client.post(
                webhook_url,
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json",
                },
                json={
                    "event": "life.review.created",
                    "review_id": str(review_id),
                },
            )
            response.raise_for_status()
    except httpx.HTTPError:
        logger.exception(
            "nextjs_revalidation_failed",
            extra={"path": "/life", "review_id": str(review_id)},
        )
        return

    logger.info(
        "nextjs_revalidation_succeeded",
        extra={"path": "/life", "review_id": str(review_id)},
    )
