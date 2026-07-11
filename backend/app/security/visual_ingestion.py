from __future__ import annotations

import hashlib
import logging
from os import getenv
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status

from app.auth import AuthUser, verify_visual_ingestion_user
from app.security.rate_limit import RedisRateLimiter, is_enabled


logger = logging.getLogger(__name__)
DEFAULT_HOURLY_LIMIT = 10


def configured_hourly_limit() -> int:
    try:
        value = int(
            getenv(
                "VISUAL_INGEST_RATE_LIMIT_PER_HOUR",
                str(DEFAULT_HOURLY_LIMIT),
            )
        )
    except ValueError:
        value = DEFAULT_HOURLY_LIMIT
    return max(1, min(value, 100))


async def enforce_visual_ingestion_rate_limit(
    request: Request,
    response: Response,
    user: Annotated[
        AuthUser,
        Depends(verify_visual_ingestion_user),
    ],
) -> None:
    if not is_enabled(
        "VISUAL_INGEST_RATE_LIMIT_ENABLED",
        default=True,
    ):
        return

    limiter: RedisRateLimiter | None = getattr(
        request.app.state,
        "visual_ingestion_rate_limiter",
        None,
    )
    if limiter is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="視覺辨識限流服務暫時無法使用",
        )

    subject = user.user_id or user.email
    digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()
    limit = configured_hourly_limit()
    try:
        decision = await limiter.check(
            key=f"rate_limit:visual_ingestion:user:{digest}",
            limit=limit,
        )
    except Exception as exc:
        logger.exception("Visual ingestion rate-limit check failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="視覺辨識限流服務暫時無法使用",
        ) from exc

    response.headers["X-RateLimit-Limit"] = str(decision.limit)
    response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
    response.headers["X-RateLimit-Reset"] = str(decision.retry_after)
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="本小時的視覺辨識額度已用完",
            headers={
                "Retry-After": str(decision.retry_after),
                "X-RateLimit-Limit": str(decision.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(decision.retry_after),
            },
        )
