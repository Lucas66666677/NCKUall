from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from os import getenv
from typing import Annotated, Any, Protocol

from fastapi import Depends, HTTPException, Request, Response, status

from app.auth import AuthUser, get_optional_user


logger = logging.getLogger(__name__)

RATE_LIMIT_LUA = """
local count = redis.call("INCR", KEYS[1])
if count == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
local ttl = redis.call("TTL", KEYS[1])
return {count, ttl}
"""


class AsyncRedis(Protocol):
    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: Any,
    ) -> Any: ...


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class RedisRateLimiter:
    """Atomic fixed-window limiter shared by every API worker."""

    def __init__(self, redis: AsyncRedis, *, window_seconds: int = 60) -> None:
        self.redis = redis
        self.window_seconds = window_seconds

    async def check(self, *, key: str, limit: int) -> RateLimitDecision:
        result = await self.redis.eval(
            RATE_LIMIT_LUA,
            1,
            key,
            self.window_seconds,
        )
        count = int(result[0])
        ttl = int(result[1])
        retry_after = ttl if ttl > 0 else self.window_seconds
        return RateLimitDecision(
            allowed=count <= limit,
            limit=limit,
            remaining=max(0, limit - count),
            retry_after=retry_after,
        )


def is_enabled(name: str, *, default: bool) -> bool:
    value = getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_client_identifier(request: Request, user: AuthUser | None) -> str:
    if user is not None:
        raw_identifier = user.user_id or user.email
        subject_type = "user"
    else:
        client_ip = request.client.host if request.client else "unknown"
        if is_enabled("TRUST_PROXY_HEADERS", default=False):
            forwarded_for = request.headers.get("x-forwarded-for")
            if forwarded_for:
                client_ip = forwarded_for.split(",", 1)[0].strip()
        raw_identifier = client_ip
        subject_type = "guest"

    digest = hashlib.sha256(raw_identifier.encode("utf-8")).hexdigest()
    return f"rate_limit:chat:{subject_type}:{digest}"


async def enforce_chat_rate_limit(
    request: Request,
    response: Response,
    user: Annotated[AuthUser | None, Depends(get_optional_user)],
) -> None:
    """Apply 5 RPM to guests and 20 RPM to verified JWT users."""

    if not is_enabled("CHAT_RATE_LIMIT_ENABLED", default=True):
        return

    limiter: RedisRateLimiter | None = getattr(
        request.app.state,
        "chat_rate_limiter",
        None,
    )
    if limiter is None:
        if is_enabled("RATE_LIMIT_FAIL_OPEN", default=False):
            logger.error("Chat rate limiter unavailable; failing open by configuration.")
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="聊天服務暫時無法使用",
        )

    limit = 20 if user is not None else 5
    try:
        decision = await limiter.check(
            key=get_client_identifier(request, user),
            limit=limit,
        )
    except Exception as exc:
        logger.exception("Redis rate-limit check failed.")
        if is_enabled("RATE_LIMIT_FAIL_OPEN", default=False):
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="聊天服務暫時無法使用",
        ) from exc

    response.headers["X-RateLimit-Limit"] = str(decision.limit)
    response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
    response.headers["X-RateLimit-Reset"] = str(decision.retry_after)

    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="請求過於頻繁，請稍後再試",
            headers={
                "Retry-After": str(decision.retry_after),
                "X-RateLimit-Limit": str(decision.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(decision.retry_after),
            },
        )
